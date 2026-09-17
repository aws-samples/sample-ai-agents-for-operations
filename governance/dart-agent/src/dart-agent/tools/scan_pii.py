# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Tool: scan_pii

Detects and optionally redacts PII in training datasets using
Amazon Comprehend's DetectPiiEntities API.

Data stays within the AWS security boundary — no training content
is sent to any external service.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

import boto3
import polars as pl
from botocore.exceptions import ClientError

import shutil

from config import Config
from tools._validation import resolve_to_local_file, validate_output_path

logger = logging.getLogger(__name__)

# Amazon Comprehend's synchronous PII API (DetectPiiEntities) processes ONE
# document per call — there is no batch PII API. We chunk only to bound the
# number of documents processed per progress-log line.
_CHUNK_SIZE = 25
# Comprehend DetectPiiEntities max bytes per document (UTF-8). Truncate defensively.
_MAX_DOC_BYTES = 4900


class PiiScanUnavailable(RuntimeError):
    """Raised when the PII scan cannot run (e.g. Comprehend access denied).

    Surfaced to the caller so the report never silently claims "no PII" when the
    scan did not actually execute — a false "clean" is the dangerous failure mode.
    """


def _load_dataframe(dataset_path: str) -> pl.DataFrame:
    """Load dataset from local path."""
    path = Path(dataset_path)
    ext = path.suffix.lower().lstrip(".")
    loaders = {
        "jsonl": pl.read_ndjson,
        "json": pl.read_json,
        "csv": pl.read_csv,
        "parquet": pl.read_parquet,
    }
    return loaders.get(ext, pl.read_ndjson)(path)


def _get_text_columns(df: pl.DataFrame) -> list[str]:
    """Return string-typed columns."""
    return [c for c in df.columns if df[c].dtype in (pl.Utf8, pl.String)]


def _scan_texts_for_pii(
    client: Any,
    texts: list[str],
    language_code: str = "en",
) -> list[list[dict]]:
    """
    Detect PII in a list of texts using Amazon Comprehend DetectPiiEntities.

    Comprehend has no batch PII operation, so each non-empty document is scanned
    with a single DetectPiiEntities call. Returns a list of PII-entity lists, one
    per input text (same order/length as the input); empty/blank inputs yield [].

    Raises:
        PiiScanUnavailable: if Comprehend rejects the request in a way that means
            the scan did not run (e.g. AccessDenied, unsupported region). This is
            raised rather than swallowed so the caller never reports a false
            "no PII" when the scan never actually executed.
    """
    results: list[list[dict]] = [[] for _ in texts]

    for i, text in enumerate(texts):
        if not text or not text.strip():
            continue
        document = text.encode("utf-8")[:_MAX_DOC_BYTES].decode("utf-8", errors="ignore")
        try:
            response = client.detect_pii_entities(
                Text=document,
                LanguageCode=language_code,
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "Unknown")
            # AccessDenied / auth / region-support failures mean the scan cannot
            # run at all — surface it instead of returning a misleading "clean".
            if code in {
                "AccessDeniedException",
                "UnrecognizedClientException",
                "InvalidRequestException",
                "UnsupportedLanguageException",
            }:
                logger.warning(json.dumps({
                    "event": "comprehend_scan_unavailable",
                    "error_code": code,
                    "error": str(e),
                }))
                raise PiiScanUnavailable(
                    f"Comprehend DetectPiiEntities failed: {code}"
                ) from e
            # Any other per-document error: log and treat that one doc as no-hit.
            logger.warning(json.dumps({
                "event": "comprehend_document_error",
                "index": i,
                "error_code": code,
            }))
            continue
        results[i] = response.get("Entities", [])
    return results


def _redact_text(text: str, entities: list[dict], strategy: str) -> str:
    """Apply redaction strategy to a text given detected PII entities."""
    if strategy == "mask":
        # Replace each entity span with [REDACTED_TYPE]
        chars = list(text)
        for entity in sorted(entities, key=lambda e: e["BeginOffset"], reverse=True):
            label = entity.get("Type", "PII")
            begin = entity["BeginOffset"]
            end = entity["EndOffset"]
            replacement = f"[REDACTED_{label}]"
            chars[begin:end] = list(replacement)
        return "".join(chars)
    # strategy == "remove" — signal caller to drop this record
    return ""


def scan_pii(
    dataset_path: str,
    redaction_strategy: str | None = None,
    produce_redacted_copy: bool = False,
    output_path: str | None = None,
) -> dict[str, Any]:
    """
    Detect and optionally redact PII in a training dataset using Amazon Comprehend.

    Scans all string columns for PII entity types defined in Config.PII_ENTITY_TYPES.
    When produce_redacted_copy is True, writes a new dataset with PII masked or
    records removed (depending on redaction_strategy). The original dataset is
    never modified.

    Args:
        dataset_path: Local file path to the dataset
        redaction_strategy: 'mask' (replace with [REDACTED_TYPE]) or 'remove'
            (delete the record). Defaults to Config.PII_REDACTION_STRATEGY.
        produce_redacted_copy: If True, write a redacted copy to output_path
        output_path: Local path for the redacted output file. Required when
            produce_redacted_copy is True.

    Returns:
        Dictionary with: pii_found, affected_record_count, entity_type_counts,
        affected_record_sample, redacted_output_path (if applicable), duration_ms
    """
    start_ts = time.time()
    # Resolve S3 or local input to a local file (threat T-1). This is what lets
    # the Amazon Comprehend PII scan run on an s3:// dataset, not just a local
    # one — previously an s3:// path could not be scanned for PII at all.
    local_path, is_tmp = resolve_to_local_file(dataset_path)
    if output_path:
        output_path = validate_output_path(output_path)  # threat E-2
    strategy = redaction_strategy or Config.PII_REDACTION_STRATEGY
    comprehend = boto3.client("comprehend", region_name=Config.AWS_REGION)

    logger.info(json.dumps({
        "event": "tool_start",
        "tool": "scan_pii",
        "strategy": strategy,
        "produce_redacted_copy": produce_redacted_copy,
    }))

    try:
        df = _load_dataframe(str(local_path))
    finally:
        if is_tmp:
            shutil.rmtree(local_path.parent, ignore_errors=True)
    total = len(df)
    text_cols = _get_text_columns(df)

    # Track results per record
    affected_records: set[int] = set()
    entity_type_counts: dict[str, int] = {}
    affected_sample: list[dict] = []
    records_to_remove: set[int] = set()

    # Per-column, per-record PII findings
    col_pii: dict[str, dict[int, list[dict]]] = {c: {} for c in text_cols}

    scan_error: str | None = None
    try:
        for col in text_cols:
            col_data = df[col].fill_null("").to_list()
            for chunk_start in range(0, total, _CHUNK_SIZE):
                chunk = col_data[chunk_start: chunk_start + _CHUNK_SIZE]
                chunk_results = _scan_texts_for_pii(comprehend, chunk)
                for local_idx, entities in enumerate(chunk_results):
                    global_idx = chunk_start + local_idx
                    filtered = [
                        e for e in entities
                        if e.get("Type") in Config.PII_ENTITY_TYPES
                    ]
                    if filtered:
                        col_pii[col][global_idx] = filtered
                        affected_records.add(global_idx)
                        for e in filtered:
                            t = e.get("Type", "UNKNOWN")
                            entity_type_counts[t] = entity_type_counts.get(t, 0) + 1
                        if len(affected_sample) < 10:
                            affected_sample.append({
                                "record_index": global_idx,
                                "column": col,
                                "entity_types": list({e["Type"] for e in filtered}),
                            })
                        if strategy == "remove":
                            records_to_remove.add(global_idx)
    except PiiScanUnavailable as e:
        # The scan could not run. Record the error so the caller/report states
        # the scan did NOT complete, rather than implying the data is clean.
        scan_error = str(e)
        logger.warning(json.dumps({
            "event": "pii_scan_incomplete",
            "reason": scan_error,
        }))

    pii_found = len(entity_type_counts) > 0
    affected_count = len(affected_records)

    # Optionally produce redacted copy
    redacted_output = None
    if produce_redacted_copy and pii_found:
        if not output_path:
            output_path = dataset_path.replace(".", "_redacted.")
        rows = df.to_dicts()
        redacted_rows = []
        for idx, row in enumerate(rows):
            if idx in records_to_remove:
                continue
            for col in text_cols:
                if idx in col_pii.get(col, {}):
                    original = row.get(col) or ""
                    row[col] = _redact_text(original, col_pii[col][idx], strategy)
            redacted_rows.append(row)
        redacted_df = pl.from_dicts(redacted_rows)
        out_path = Path(output_path)
        ext = out_path.suffix.lower().lstrip(".")
        if ext == "jsonl":
            redacted_df.write_ndjson(out_path)
        elif ext == "csv":
            redacted_df.write_csv(out_path)
        elif ext == "parquet":
            redacted_df.write_parquet(out_path)
        else:
            redacted_df.write_ndjson(out_path)
        redacted_output = str(out_path)
        logger.info(json.dumps({
            "event": "redacted_copy_written",
            "output_path": redacted_output,
            "records_removed": len(records_to_remove),
        }))

    duration_ms = round((time.time() - start_ts) * 1000, 1)

    result = {
        "pii_found": pii_found,
        "scan_complete": scan_error is None,
        "scan_error": scan_error,
        "total_records": total,
        "affected_record_count": affected_count,
        "entity_type_counts": entity_type_counts,
        "affected_record_sample": affected_sample,
        "redacted_output_path": redacted_output,
        "records_removed": len(records_to_remove),
        "duration_ms": duration_ms,
    }

    logger.info(json.dumps({
        "event": "tool_complete",
        "tool": "scan_pii",
        "pii_found": pii_found,
        "affected_records": affected_count,
        "duration_ms": duration_ms,
        "success": True,
    }))

    return result
