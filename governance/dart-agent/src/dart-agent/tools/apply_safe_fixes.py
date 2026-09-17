# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Tool: apply_safe_fixes

Applies safe, non-destructive dataset fixes automatically:
  - Remove exact duplicates
  - Remove records with empty/null required fields (< MIN_RESPONSE_TOKENS)
  - Normalise text encoding to UTF-8

The original dataset is NEVER modified. All fixes produce a new output file.
PII redaction and near-duplicate removal require explicit user approval and
are handled by scan_pii and detect_duplicates respectively.
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
from tools._validation import resolve_to_local_file, validate_output_path

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


def _write_dataframe(df: pl.DataFrame, output_path: str) -> None:
    path = Path(output_path)
    ext = path.suffix.lower().lstrip(".")
    writers = {
        "jsonl": df.write_ndjson,
        "json": df.write_json,
        "csv": df.write_csv,
        "parquet": df.write_parquet,
    }
    writer = writers.get(ext, df.write_ndjson)
    writer(path)


def _record_hash(record: dict) -> str:
    text = json.dumps(record, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Response/target fields across the known fine-tuning schemas. The token-length
# rule is applied to these fields only — short prompts, instructions, questions
# or titles are legitimate and must NOT cause a record to be dropped.
_RESPONSE_FIELDS = ("response", "completion", "output", "answer", "target")


def _is_empty_record(record: dict, enc: tiktoken.Encoding) -> bool:
    """Return True if the record should be dropped as empty/insufficient.

    A record is considered empty when:
      - any string field is present but blank (whitespace-only), or
      - a *response/target* field has fewer than MIN_RESPONSE_TOKENS tokens.

    The token-length threshold is deliberately applied only to response-type
    fields (see _RESPONSE_FIELDS). Short instructions/prompts/questions are
    valid training signal and must not trigger removal.
    """
    # Drop records with a blank (whitespace-only) value in any string field.
    for v in record.values():
        if isinstance(v, str) and v == "":
            return True
        if isinstance(v, str) and not v.strip():
            return True

    # Apply the minimum-token rule to response-type fields only.
    present_response_fields = [
        k for k in record
        if k.lower() in _RESPONSE_FIELDS and isinstance(record[k], str)
    ]
    for k in present_response_fields:
        value = record[k]
        if value.strip() and len(enc.encode(value)) < Config.MIN_RESPONSE_TOKENS:
            return True

    return False


def _fix_encoding(text: str) -> str:
    """Re-encode text through UTF-8, replacing undecodable characters."""
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def apply_safe_fixes(
    dataset_path: str,
    output_path: str | None = None,
    remove_exact_duplicates: bool = True,
    remove_empty_records: bool = True,
    fix_encoding: bool = True,
) -> dict[str, Any]:
    """
    Apply safe, automatically reversible fixes to a training dataset.

    Safe fixes (no approval required):
    - Remove exact duplicate records (identical content)
    - Remove records where any text field is empty or < MIN_RESPONSE_TOKENS tokens
    - Normalise text encoding to UTF-8

    The original file is never modified. A new file is written to output_path.
    Record content is never altered beyond encoding normalisation.

    Args:
        dataset_path: Local path to the input dataset
        output_path: Local path for the fixed output dataset. If None, appends
            '_fixed' to the input filename.
        remove_exact_duplicates: Whether to remove exact duplicate records
        remove_empty_records: Whether to remove empty/near-empty records
        fix_encoding: Whether to normalise text encoding to UTF-8

    Returns:
        Dictionary with: records_before, records_after, exact_duplicates_removed,
        empty_records_removed, encoding_issues_fixed, output_path, duration_ms
    """
    start_ts = time.time()
    # Resolve S3 or local input to a local file (threat T-1) so safe-fixes can
    # run on an s3:// dataset. Keep the original path only to derive a sensible
    # default output filename below.
    original_path = dataset_path
    local_path, is_tmp = resolve_to_local_file(dataset_path)
    if output_path:
        output_path = validate_output_path(output_path)  # threat E-2
    logger.info(json.dumps({
        "event": "tool_start",
        "tool": "apply_safe_fixes",
        "dataset_path": original_path,
    }))

    df = _load_dataframe(str(local_path))
    records_before = len(df)
    enc = tiktoken.get_encoding("cl100k_base")
    text_cols = [c for c in df.columns if df[c].dtype in (pl.Utf8, pl.String)]

    rows = df.to_dicts()
    exact_dupes_removed = 0
    empty_removed = 0
    encoding_fixed = 0

    seen_hashes: set[str] = set()
    clean_rows: list[dict] = []

    for row in rows:
        # Fix encoding first
        if fix_encoding:
            for col in text_cols:
                if isinstance(row.get(col), str):
                    fixed = _fix_encoding(row[col])
                    if fixed != row[col]:
                        row[col] = fixed
                        encoding_fixed += 1

        # Remove exact duplicates
        if remove_exact_duplicates:
            h = _record_hash(row)
            if h in seen_hashes:
                exact_dupes_removed += 1
                continue
            seen_hashes.add(h)

        # Remove empty records
        if remove_empty_records:
            if _is_empty_record(row, enc):
                empty_removed += 1
                continue

        clean_rows.append(row)

    records_after = len(clean_rows)

    # Derive output path. For a local input, write the cleaned copy alongside it
    # with a _fixed suffix. For an S3 input (no explicit output_path), base the
    # name on the object's filename and write it beside the temp download.
    if not output_path:
        base = Path(original_path.split("/")[-1]) if original_path.startswith("s3://") else Path(original_path)
        stem = base.stem or "dataset"
        suffix = base.suffix or ".jsonl"
        output_path = str(local_path.parent / f"{stem}_fixed{suffix}")

    if clean_rows:
        clean_df = pl.from_dicts(clean_rows)
        _write_dataframe(clean_df, output_path)
    else:
        # Write empty file with same schema
        pl.from_dicts([{c: None for c in df.columns}]).clear().write_ndjson(output_path)

    # The input temp download (if any) is no longer needed; the cleaned output
    # lives in the same temp dir for S3 inputs, so only remove the input temp
    # dir when we did NOT write our output into it.
    if is_tmp and not str(output_path).startswith(str(local_path.parent)):
        shutil.rmtree(local_path.parent, ignore_errors=True)

    duration_ms = round((time.time() - start_ts) * 1000, 1)

    result = {
        "records_before": records_before,
        "records_after": records_after,
        "records_removed_total": records_before - records_after,
        "exact_duplicates_removed": exact_dupes_removed,
        "empty_records_removed": empty_removed,
        "encoding_issues_fixed": encoding_fixed,
        "output_path": output_path,
        "duration_ms": duration_ms,
    }

    logger.info(json.dumps({
        "event": "tool_complete",
        "tool": "apply_safe_fixes",
        "records_before": records_before,
        "records_after": records_after,
        "duration_ms": duration_ms,
        "success": True,
    }))

    return result
