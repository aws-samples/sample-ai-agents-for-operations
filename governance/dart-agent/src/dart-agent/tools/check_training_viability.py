# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Tool: check_training_viability

Validates whether a dataset is structurally compatible with the target
model for fine-tuning: token budget, train/val leakage, minimum size,
schema completeness, and format requirements.
"""

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import polars as pl
import tiktoken

import shutil

from config import Config
from tools._validation import resolve_to_local_file

logger = logging.getLogger(__name__)


def _load_dataframe(dataset_path: str) -> pl.DataFrame:
    path = Path(dataset_path)
    ext = path.suffix.lower().lstrip(".")
    loaders = {
        "jsonl": pl.read_ndjson,
        "json": pl.read_json,
        "csv": pl.read_csv,
        "parquet": pl.read_parquet,
    }
    return loaders.get(ext, pl.read_ndjson)(path)


def _get_tokenizer(model_id: str) -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def _extract_model_size(model_id: str) -> str:
    """Extract rough model size category from model ID string."""
    model_lower = model_id.lower()
    for size in ("70b", "34b", "13b", "8b", "7b"):
        if size in model_lower:
            return size
    return "default"


def _check_token_budget(
    df: pl.DataFrame,
    model_id: str,
    enc: tiktoken.Encoding,
) -> dict:
    """Check if total token count is within model's context budget."""
    context_limit = Config.MODEL_CONTEXT_LIMITS.get(model_id, 128000)
    text_cols = [c for c in df.columns if df[c].dtype in (pl.Utf8, pl.String)]

    total_tokens = 0
    per_record_max = 0
    over_limit_count = 0

    for row in df.to_dicts():
        record_text = " ".join(str(v) for v in row.values() if v and isinstance(v, str))
        token_count = len(enc.encode(record_text))
        total_tokens += token_count
        per_record_max = max(per_record_max, token_count)
        if token_count > context_limit:
            over_limit_count += 1

    return {
        "total_tokens": total_tokens,
        "per_record_max_tokens": per_record_max,
        "model_context_limit": context_limit,
        "records_exceeding_context": over_limit_count,
        "within_budget": over_limit_count == 0,
    }


def _check_train_val_leakage(
    train_path: str,
    val_path: str | None,
) -> dict:
    """Detect exact record overlap between train and validation splits."""
    if not val_path:
        return {"leakage_checked": False, "reason": "No validation path provided"}

    train_df = _load_dataframe(train_path)
    val_df = _load_dataframe(val_path)

    def hash_records(df: pl.DataFrame) -> set[str]:
        hashes = set()
        for row in df.to_dicts():
            text = json.dumps(row, sort_keys=True)
            hashes.add(hashlib.sha256(text.encode()).hexdigest())
        return hashes

    train_hashes = hash_records(train_df)
    val_hashes = hash_records(val_df)
    leaked = train_hashes & val_hashes

    return {
        "leakage_checked": True,
        "train_records": len(train_df),
        "val_records": len(val_df),
        "leaked_record_count": len(leaked),
        "leakage_rate": round(len(leaked) / len(val_df), 4) if len(val_df) > 0 else 0.0,
        "leakage_detected": len(leaked) > 0,
    }


def _check_schema_completeness(df: pl.DataFrame) -> dict:
    """Validate dataset schema against known fine-tuning formats."""
    col_set = set(c.lower() for c in df.columns)
    matches = []
    for pattern, required_fields in Config.KNOWN_SCHEMAS.items():
        if all(f in col_set for f in required_fields):
            matches.append(pattern)

    missing_fields_by_pattern = {}
    for pattern, required_fields in Config.KNOWN_SCHEMAS.items():
        missing = [f for f in required_fields if f not in col_set]
        if missing:
            missing_fields_by_pattern[pattern] = missing

    return {
        "matched_patterns": matches,
        "schema_valid": len(matches) > 0,
        "columns_found": list(df.columns),
        "missing_fields_by_pattern": missing_fields_by_pattern,
    }


def _check_minimum_size(df: pl.DataFrame, model_id: str) -> dict:
    """Check if dataset meets minimum record count for the target model."""
    model_size = _extract_model_size(model_id)
    min_required = Config.MODEL_MIN_RECORDS.get(model_size, Config.MODEL_MIN_RECORDS["default"])
    total = len(df)
    return {
        "total_records": total,
        "minimum_recommended": min_required,
        "meets_minimum": total >= min_required,
        "model_size_category": model_size,
        "shortfall": max(0, min_required - total),
    }


def check_training_viability(
    dataset_path: str,
    target_model: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    validation_path: str | None = None,
) -> dict[str, Any]:
    """
    Validate whether a dataset is ready for LLM fine-tuning on the target model.

    Checks: token budget vs model context limit, train/validation data leakage,
    minimum dataset size for the target model size category, schema completeness
    for known fine-tuning formats, and records exceeding context limits.

    Args:
        dataset_path: Local file path to the training dataset
        target_model: Bedrock model ID being fine-tuned
        validation_path: Optional local path to the validation split for
            leakage detection

    Returns:
        Dictionary with: token_budget, schema_check, size_check, leakage_check,
        overall_viable, blocking_issues, warnings, duration_ms
    """
    start_ts = time.time()
    # Resolve S3 or local inputs to local files (threat T-1) so viability checks
    # run on s3:// datasets, not just local ones.
    local_path, ds_tmp = resolve_to_local_file(dataset_path)
    val_local: str | None = None
    val_tmp = False
    if validation_path:
        _vp, val_tmp = resolve_to_local_file(validation_path)
        val_local = str(_vp)
    logger.info(json.dumps({
        "event": "tool_start",
        "tool": "check_training_viability",
        "target_model": target_model,
    }))

    try:
        df = _load_dataframe(str(local_path))
        enc = _get_tokenizer(target_model)

        token_budget = _check_token_budget(df, target_model, enc)
        schema_check = _check_schema_completeness(df)
        size_check = _check_minimum_size(df, target_model)
        leakage_check = _check_train_val_leakage(str(local_path), val_local)
    finally:
        if ds_tmp:
            shutil.rmtree(local_path.parent, ignore_errors=True)
        if val_tmp and val_local:
            shutil.rmtree(Path(val_local).parent, ignore_errors=True)

    # Determine blocking issues and warnings
    blocking_issues = []
    warnings = []

    if token_budget["records_exceeding_context"] > 0:
        blocking_issues.append(
            f"{token_budget['records_exceeding_context']} records exceed the "
            f"{target_model} context limit of {token_budget['model_context_limit']:,} tokens"
        )

    if not schema_check["schema_valid"]:
        warnings.append(
            f"Dataset schema does not match any known fine-tuning format. "
            f"Columns found: {schema_check['columns_found']}"
        )

    if not size_check["meets_minimum"]:
        warnings.append(
            f"Dataset has {size_check['total_records']:,} records but "
            f"{size_check['minimum_recommended']:,} are recommended for a "
            f"{size_check['model_size_category']} model"
        )

    if leakage_check.get("leakage_detected"):
        blocking_issues.append(
            f"Train/validation data leakage detected: "
            f"{leakage_check['leaked_record_count']} records appear in both splits "
            f"({leakage_check['leakage_rate']*100:.1f}% of validation set)"
        )

    overall_viable = len(blocking_issues) == 0
    duration_ms = round((time.time() - start_ts) * 1000, 1)

    result = {
        "token_budget": token_budget,
        "schema_check": schema_check,
        "size_check": size_check,
        "leakage_check": leakage_check,
        "overall_viable": overall_viable,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
        "duration_ms": duration_ms,
    }

    logger.info(json.dumps({
        "event": "tool_complete",
        "tool": "check_training_viability",
        "overall_viable": overall_viable,
        "blocking_issues_count": len(blocking_issues),
        "duration_ms": duration_ms,
        "success": True,
    }))

    return result
