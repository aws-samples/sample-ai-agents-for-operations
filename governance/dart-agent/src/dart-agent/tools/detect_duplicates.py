# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Tool: detect_duplicates

Detects exact and near-duplicate records in a dataset using hash-based
exact matching and MinHash Locality-Sensitive Hashing for fuzzy matching.
"""

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import polars as pl
from datasketch import MinHash, MinHashLSH

from config import Config
from tools._validation import validate_dataset_path

logger = logging.getLogger(__name__)


def _load_dataframe(dataset_path: str) -> pl.DataFrame:
    """Load a dataset from a local path into a Polars DataFrame."""
    path = Path(dataset_path)
    ext = path.suffix.lower().lstrip(".")
    loaders = {
        "jsonl": pl.read_ndjson,
        "json": pl.read_json,
        "csv": pl.read_csv,
        "parquet": pl.read_parquet,
    }
    loader = loaders.get(ext, pl.read_ndjson)
    return loader(path)


def _record_to_text(record: dict) -> str:
    """Flatten a record dict to a single normalised text string for hashing."""
    parts = []
    for v in record.values():
        if isinstance(v, str):
            parts.append(v.strip().lower())
        elif v is not None:
            parts.append(str(v).strip().lower())
    return " ".join(parts)


def _text_to_shingles(text: str, k: int = 3) -> set[str]:
    """Convert text to k-char shingles for MinHash."""
    text = re.sub(r"\s+", " ", text).strip()
    return {text[i: i + k] for i in range(max(len(text) - k + 1, 1))}


def _compute_minhash(text: str, num_perm: int) -> MinHash:
    """Compute a MinHash signature for a text string."""
    mh = MinHash(num_perm=num_perm)
    for shingle in _text_to_shingles(text):
        mh.update(shingle.encode("utf-8"))
    return mh


def detect_duplicates(
    dataset_path: str,
    similarity_threshold: float | None = None,
    text_columns: list[str] | None = None,
) -> dict[str, Any]:
    """
    Detect exact and near-duplicate records in a training dataset.

    Uses SHA-256 hashing for exact duplicate detection and MinHash LSH
    for near-duplicate detection based on character shingle similarity.
    Near-duplicate detection is configurable via similarity_threshold.

    Args:
        dataset_path: Local file path to the dataset (post-download from S3)
        similarity_threshold: Jaccard similarity threshold (0.0–1.0). Defaults
            to Config.DEDUP_SIMILARITY_THRESHOLD (0.85)
        text_columns: List of column names to use for dedup. If None, all
            string columns are concatenated.

    Returns:
        Dictionary with: exact_duplicate_count, near_duplicate_count,
        exact_duplicate_indices, near_duplicate_pairs (sample),
        duplicate_rate, wasted_token_estimate, duration_ms
    """
    start_ts = time.time()
    dataset_path = validate_dataset_path(dataset_path)  # threat T-1
    threshold = similarity_threshold or Config.DEDUP_SIMILARITY_THRESHOLD
    num_perm = Config.MINHASH_NUM_PERM

    logger.info(json.dumps({
        "event": "tool_start",
        "tool": "detect_duplicates",
        "threshold": threshold,
        "num_perm": num_perm,
    }))

    df = _load_dataframe(dataset_path)
    total = len(df)

    # Determine text columns
    if text_columns:
        cols = [c for c in text_columns if c in df.columns]
    else:
        cols = [c for c in df.columns if df[c].dtype in (pl.Utf8, pl.String)]

    records = df.select(cols).to_dicts()

    # ── Exact deduplication ───────────────────────────────────────────────────
    seen_hashes: dict[str, int] = {}
    exact_dup_indices: list[int] = []

    for idx, rec in enumerate(records):
        text = _record_to_text(rec)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            exact_dup_indices.append(idx)
        else:
            seen_hashes[digest] = idx

    # ── Near-duplicate detection via MinHash LSH ──────────────────────────────
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    minhashes: dict[int, MinHash] = {}
    near_dup_pairs: list[tuple[int, int]] = []
    near_dup_indices: set[int] = set()

    # Only process non-exact-duplicate records
    exact_set = set(exact_dup_indices)
    for idx, rec in enumerate(records):
        if idx in exact_set:
            continue
        text = _record_to_text(rec)
        mh = _compute_minhash(text, num_perm)
        minhashes[idx] = mh
        key = str(idx)
        result = lsh.query(mh)
        for match_key in result:
            pair = (min(idx, int(match_key)), max(idx, int(match_key)))
            if pair not in near_dup_pairs:
                near_dup_pairs.append(pair)
                near_dup_indices.add(idx)
                near_dup_indices.add(int(match_key))
        lsh.insert(key, mh)

    exact_count = len(exact_dup_indices)
    near_count = len(near_dup_indices) - exact_count  # avoid double-counting
    near_count = max(near_count, 0)
    total_dup = exact_count + near_count
    duplicate_rate = round(total_dup / total, 4) if total > 0 else 0.0

    duration_ms = round((time.time() - start_ts) * 1000, 1)

    result = {
        "total_records": total,
        "exact_duplicate_count": exact_count,
        "near_duplicate_count": near_count,
        "total_duplicate_count": total_dup,
        "duplicate_rate": duplicate_rate,
        "exact_duplicate_indices": exact_dup_indices[:100],   # capped for response size
        "near_duplicate_pairs_sample": near_dup_pairs[:20],   # sample only
        "similarity_threshold_used": threshold,
        "duration_ms": duration_ms,
    }

    logger.info(json.dumps({
        "event": "tool_complete",
        "tool": "detect_duplicates",
        "exact_duplicates": exact_count,
        "near_duplicates": near_count,
        "duplicate_rate": duplicate_rate,
        "duration_ms": duration_ms,
        "success": True,
    }))

    return result
