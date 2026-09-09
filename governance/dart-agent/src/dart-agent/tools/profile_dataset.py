# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Tool: profile_dataset

Analyses a dataset file and returns a comprehensive structural profile:
format, schema, null rates, text length stats, token estimates, encoding.
"""

import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import boto3
import chardet
import polars as pl
import tiktoken
from botocore.exceptions import ClientError

from config import Config
from tools._validation import validate_dataset_path, validate_s3_uri

logger = logging.getLogger(__name__)

# Tokenizer families keyed by model ID prefix
_TOKENIZER_MAP = {
    "anthropic": "cl100k_base",
    "amazon": "cl100k_base",
    "meta": "cl100k_base",
    "mistral": "cl100k_base",
}


def _get_tokenizer(model_id: str) -> tiktoken.Encoding:
    """Return the appropriate tiktoken encoding for the target model."""
    prefix = model_id.split(".")[0] if "." in model_id else "default"
    encoding_name = _TOKENIZER_MAP.get(prefix, "cl100k_base")
    return tiktoken.get_encoding(encoding_name)


def _download_s3_to_tmp(s3_path: str) -> Path:
    """
    Download an S3 object to a securely created temporary file.

    Uses tempfile to create an unpredictable, permission-restricted temp
    directory rather than a hardcoded /tmp path (avoids bandit B108 and
    predictable-path tampering). Callers are responsible for cleaning up the
    returned path when finished.
    """
    s3 = boto3.client("s3", region_name=Config.AWS_REGION)
    # Validate + parse the S3 URI (allowlist, traversal guard) — threat T-1.
    bucket, key = validate_s3_uri(s3_path)

    # Secure temp dir (0700) with a random name; safe filename derived from key.
    tmp_dir = tempfile.mkdtemp(prefix="dart_")
    safe_name = Path(key).name or "dataset"
    local_path = Path(tmp_dir) / safe_name
    try:
        s3.download_file(bucket, key, str(local_path))
    except ClientError as e:
        logger.warning(json.dumps({
            "event": "s3_download_error",
            "bucket": bucket,
            "key": key,
            "error": str(e),
        }))
        raise
    return local_path


def _detect_encoding(file_path: Path) -> str:
    """Detect file encoding by sampling the first 64KB."""
    with open(file_path, "rb") as f:
        sample = f.read(65536)
    result = chardet.detect(sample)
    return result.get("encoding") or "utf-8"


def _load_dataset(file_path: Path, file_format: str) -> pl.DataFrame:
    """Load dataset into a Polars DataFrame based on format."""
    loaders = {
        "jsonl": lambda p: pl.read_ndjson(p),
        "json": lambda p: pl.read_json(p),
        "csv": lambda p: pl.read_csv(p, infer_schema_length=1000),
        "parquet": lambda p: pl.read_parquet(p),
        "txt": lambda p: pl.DataFrame({"text": p.read_text().splitlines()}),
    }
    loader = loaders.get(file_format)
    if not loader:
        raise ValueError(f"Unsupported format: {file_format}")
    return loader(file_path)


def _detect_format(file_path: Path) -> str:
    """Detect file format from extension, with content sniffing fallback."""
    ext = file_path.suffix.lower().lstrip(".")
    if ext in ("jsonl", "json", "csv", "parquet", "txt"):
        return ext
    # Content sniff
    with open(file_path, "rb") as f:
        header = f.read(512)
    if header.strip().startswith(b"{") or header.strip().startswith(b"["):
        return "json"
    return "txt"


def _compute_text_stats(series: pl.Series, enc: tiktoken.Encoding) -> dict:
    """Compute length statistics for a text column."""
    lengths = series.drop_nulls().map_elements(len, return_dtype=pl.Int64)
    token_counts = series.drop_nulls().map_elements(
        lambda t: len(enc.encode(t)), return_dtype=pl.Int64
    )
    return {
        "char_min": int(lengths.min() or 0),
        "char_max": int(lengths.max() or 0),
        "char_mean": round(float(lengths.mean() or 0), 1),
        "char_p50": int(lengths.quantile(0.5) or 0),
        "char_p95": int(lengths.quantile(0.95) or 0),
        "token_min": int(token_counts.min() or 0),
        "token_max": int(token_counts.max() or 0),
        "token_mean": round(float(token_counts.mean() or 0), 1),
        "token_total": int(token_counts.sum() or 0),
    }


def profile_dataset(dataset_path: str, target_model: str = "anthropic.claude-sonnet-4-20250514-v1:0") -> dict[str, Any]:
    """
    Analyse a dataset file and return a comprehensive structural profile.

    Detects format, schema, null rates, text length distributions, token
    estimates for the target model's tokenizer, encoding issues, and
    records with empty or near-empty text fields.

    Args:
        dataset_path: S3 path (s3://bucket/key) or local file path to the dataset
        target_model: Bedrock model ID used to select the appropriate tokenizer

    Returns:
        Dictionary with keys: format, total_records, schema, text_stats,
        encoding, empty_record_count, quality_flags, profiling_duration_ms
    """
    start_ts = time.time()
    logger.info(json.dumps({
        "event": "tool_start",
        "tool": "profile_dataset",
        "dataset_path": dataset_path,
        "target_model": target_model,
    }))

    # Validate the caller-supplied path before any filesystem/S3 access (T-1).
    validated_path = validate_dataset_path(dataset_path, must_exist=False)

    # Resolve file path. Track any temp download so it can be cleaned up.
    tmp_download_dir: str | None = None
    if validated_path.startswith("s3://"):
        file_path = _download_s3_to_tmp(validated_path)
        tmp_download_dir = str(file_path.parent)
    else:
        file_path = Path(validated_path)

    try:
        if not file_path.exists():
            raise FileNotFoundError(f"Dataset file not found: {file_path}")

        file_format = _detect_format(file_path)
        encoding = _detect_encoding(file_path)
        df = _load_dataset(file_path, file_format)

        total_records = len(df)
        enc = _get_tokenizer(target_model)
        file_size_bytes = file_path.stat().st_size

        # Schema: field name → {dtype, null_rate, cardinality}
        schema = {}
        for col in df.columns:
            null_count = df[col].null_count()
            null_rate = round(null_count / total_records, 4) if total_records > 0 else 0.0
            unique_count = df[col].n_unique()
            schema[col] = {
                "dtype": str(df[col].dtype),
                "null_rate": null_rate,
                "cardinality": int(unique_count),
            }

        # Text stats for string columns
        text_stats = {}
        for col in df.columns:
            if df[col].dtype == pl.Utf8 or df[col].dtype == pl.String:
                text_stats[col] = _compute_text_stats(df[col], enc)

        # Detect empty / near-empty records
        empty_record_count = 0
        empty_record_indices = []
        for col, stats in text_stats.items():
            mask = df[col].map_elements(
                lambda t: t is None or len(enc.encode(str(t))) < Config.MIN_RESPONSE_TOKENS,
                return_dtype=pl.Boolean,
            )
            indices = df.with_row_index().filter(mask)["index"].to_list()
            empty_record_count += len(indices)
            empty_record_indices.extend(indices[:10])  # keep first 10 as examples

        # Detect mixed encoding
        encoding_flag = encoding.lower() not in ("utf-8", "utf-8-sig", "ascii")

        # Detect schema pattern
        detected_schema_pattern = None
        col_set = set(c.lower() for c in df.columns)
        for pattern_name, required_fields in Config.KNOWN_SCHEMAS.items():
            if all(f in col_set for f in required_fields):
                detected_schema_pattern = pattern_name
                break

        duration_ms = round((time.time() - start_ts) * 1000, 1)

        result = {
            "format": file_format,
            "total_records": total_records,
            "file_size_bytes": file_size_bytes,
            "encoding": encoding,
            "encoding_warning": encoding_flag,
            "schema": schema,
            "detected_schema_pattern": detected_schema_pattern,
            "text_stats": text_stats,
            "empty_record_count": empty_record_count,
            "empty_record_sample_indices": list(set(empty_record_indices))[:10],
            "quality_flags": {
                "mixed_encoding": encoding_flag,
                "has_empty_records": empty_record_count > 0,
                "schema_recognized": detected_schema_pattern is not None,
            },
            "profiling_duration_ms": duration_ms,
        }

        logger.info(json.dumps({
            "event": "tool_complete",
            "tool": "profile_dataset",
            "total_records": total_records,
            "duration_ms": duration_ms,
            "success": True,
        }))

        return result
    finally:
        # Clean up any temp file downloaded from S3 (avoids leaving customer
        # data on ephemeral local storage — threat I-2).
        if tmp_download_dir:
            shutil.rmtree(tmp_download_dir, ignore_errors=True)
