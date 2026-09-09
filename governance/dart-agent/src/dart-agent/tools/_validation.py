# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Shared input validation for DART tools.

Addresses threat T-1 (path traversal / arbitrary read via unvalidated
dataset_path). Every tool that accepts a caller-supplied dataset path or
output path routes it through these helpers before touching S3 or the
local filesystem.

Two configurable controls (see config.Config):
  - ALLOWED_S3_BUCKETS / ALLOWED_S3_PREFIXES: allowlist for s3:// inputs.
    Empty allowlist = allow any bucket (documented default for the public
    sample; operators SHOULD set these in production).
  - ALLOWED_LOCAL_BASE_DIR: local paths must resolve to a location inside
    this directory. Empty = allow any local path (dev/CLI convenience).

The functions raise ValueError on rejection so callers surface a clear,
non-leaky error rather than performing the read.
"""

from __future__ import annotations

import re
from pathlib import Path

from config import Config

_S3_URI_RE = re.compile(r"^s3://(?P<bucket>[^/]+)/(?P<key>.+)$")
# S3 bucket naming: 3-63 chars, lowercase letters, digits, dots, hyphens.
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")


def _reject(reason: str) -> None:
    raise ValueError(f"Rejected dataset path: {reason}")


def validate_s3_uri(s3_path: str) -> tuple[str, str]:
    """
    Validate an s3:// URI and return (bucket, key).

    Enforces well-formed bucket/key, blocks key traversal segments, and
    applies the configured bucket/prefix allowlists.
    """
    match = _S3_URI_RE.match(s3_path.strip())
    if not match:
        _reject("malformed S3 URI (expected s3://bucket/key)")
    bucket = match.group("bucket")
    key = match.group("key")

    if not _S3_BUCKET_RE.match(bucket):
        _reject(f"invalid S3 bucket name: {bucket!r}")

    # Block traversal / absolute-style keys.
    if key.startswith("/") or ".." in Path(key).parts:
        _reject("S3 key contains path traversal segments")

    allowed_buckets = Config.ALLOWED_S3_BUCKETS
    if allowed_buckets and bucket not in allowed_buckets:
        _reject(f"S3 bucket {bucket!r} is not in the allowlist")

    allowed_prefixes = Config.ALLOWED_S3_PREFIXES
    if allowed_prefixes and not any(key.startswith(p) for p in allowed_prefixes):
        _reject("S3 key does not match any allowed prefix")

    return bucket, key


def validate_local_path(path_str: str, *, must_exist: bool = True) -> Path:
    """
    Validate a local filesystem path and return the resolved Path.

    Blocks traversal outside ALLOWED_LOCAL_BASE_DIR (when configured) and,
    optionally, requires the file to exist.
    """
    if not path_str or not path_str.strip():
        _reject("empty local path")

    resolved = Path(path_str).expanduser().resolve()

    base = Config.ALLOWED_LOCAL_BASE_DIR
    if base:
        base_resolved = Path(base).expanduser().resolve()
        if not resolved.is_relative_to(base_resolved):
            _reject(f"local path escapes the allowed base directory {base_resolved}")

    if must_exist and not resolved.exists():
        # FileNotFoundError is the more precise type for a missing file.
        raise FileNotFoundError(f"Dataset file not found: {resolved}")

    return resolved


def validate_dataset_path(dataset_path: str, *, must_exist: bool = True) -> str:
    """
    Validate any caller-supplied dataset path (S3 URI or local path).

    For S3 URIs the original s3:// string is returned (after validation) so
    downloaders keep working. For local paths the resolved, validated path
    string is returned.
    """
    if not dataset_path or not dataset_path.strip():
        _reject("empty dataset path")

    if dataset_path.startswith("s3://"):
        validate_s3_uri(dataset_path)
        return dataset_path

    return str(validate_local_path(dataset_path, must_exist=must_exist))


def validate_output_path(output_path: str) -> str:
    """
    Validate a caller-supplied local output path (need not exist yet).

    Ensures the parent directory is inside ALLOWED_LOCAL_BASE_DIR when set,
    blocking writes to arbitrary container locations (threat E-2).
    """
    if not output_path or not output_path.strip():
        _reject("empty output path")

    resolved = Path(output_path).expanduser().resolve()
    base = Config.ALLOWED_LOCAL_BASE_DIR
    if base:
        base_resolved = Path(base).expanduser().resolve()
        if not resolved.is_relative_to(base_resolved):
            _reject(f"output path escapes the allowed base directory {base_resolved}")
    return str(resolved)
