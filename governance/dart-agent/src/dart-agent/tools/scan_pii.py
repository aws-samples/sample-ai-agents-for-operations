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

from config import Config
from tools._validation import validate_dataset_path, validate_output_path

logger = logging.getLogger(__name__)

_BATCH_SIZE = 25  # Amazon Comprehend batch limit


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


def _call_comprehend_batch(
    client: Any,
    texts: list[str],
    language_code: str = "en",
) -> list[list[dict]]:
    """
    Call Amazon Comprehend BatchDetectPiiEntities for a batch of texts.

    Uses the batch API (up to 25 documents per call) so the number of API
    calls scales with dataset_size / 25 rather than one call per record.
    Returns a list of PII entity lists, one per input text (same order/length
    as the input). Empty/blank inputs are skipped and yield an empty list.

    Note: BatchDetectPiiEntities requires 1–25 non-empty documents per call, so
    we compact out blanks, send only the non-empty docs, and map results back
    to their original positions.
    """
    results: list[list[dict]] = [[] for _ in texts]

    # Comprehend has a per-document byte limit — truncate defensively.
    non_empty: list[tuple[int, str]] = [
        (i, t[:4900]) for i, t in enumerate(texts) if t and t.strip()
    ]
    if not non_empty:
        return results

    original_indices = [i for i, _ in non_empty]
    documents = [t for _, t in non_empty]

    try:
        response = client.batch_detect_pii_entities(
            TextList=documents,
            LanguageCode=language_code,
        )
        # ResultList entries carry their position via the "Index" field.
        for item in response.get("ResultList", []):
            local_idx = item.get("Index")
            if local_idx is None or local_idx >= len(original_indices):
                continue
            results[original_indices[local_idx]] = item.get("Entities", [])
        # Per-document errors, if any, are logged but non-fatal.
        for err in response.get("ErrorList", []):
            logger.warning(json.dumps({
                "event": "comprehend_batch_item_error",
                "batch_index": err.get("Index"),
                "error_code": err.get("ErrorCode"),
            }))
    except ClientError as e:
        logger.warning(json.dumps({
            "event": "comprehend_batch_error",
            "error": str(e),
            "batch_size": len(documents),
        }))
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
    dataset_path = validate_dataset_path(dataset_path)  # threat T-1
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

    df = _load_dataframe(dataset_path)
    total = len(df)
    text_cols = _get_text_columns(df)

    # Track results per record
    affected_records: set[int] = set()
    entity_type_counts: dict[str, int] = {}
    affected_sample: list[dict] = []
    records_to_remove: set[int] = set()

    # Per-column, per-record PII findings
    col_pii: dict[str, dict[int, list[dict]]] = {c: {} for c in text_cols}

    for col in text_cols:
        col_data = df[col].fill_null("").to_list()
        # Process in batches
        for batch_start in range(0, total, _BATCH_SIZE):
            batch = col_data[batch_start: batch_start + _BATCH_SIZE]
            batch_results = _call_comprehend_batch(comprehend, batch)
            for local_idx, entities in enumerate(batch_results):
                global_idx = batch_start + local_idx
                # Filter to configured entity types only
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
