# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""S3 object reading with file extension validation."""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SUPPORTED_EXTENSIONS: set[str] = {".csv", ".json", ".txt"}


def _is_valid_bucket_name(bucket: str) -> bool:
    """Validate S3 bucket name format (3-63 chars, lowercase alphanumeric + hyphens/dots)."""
    import re
    return bool(re.match(r'^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$', bucket))


def is_supported_extension(key: str) -> bool:
    """Return True if the file key ends with a supported extension (case-insensitive)."""
    _, ext = os.path.splitext(key)
    return ext.lower() in SUPPORTED_EXTENSIONS


def read_routing_document(bucket: str, key: str) -> str | None:
    """Read S3 object content. Returns None if unsupported extension or on error."""
    # Input validation: bucket name and key
    if not bucket or not _is_valid_bucket_name(bucket):
        logger.error(json.dumps({"message": "Invalid bucket name", "bucket": bucket}))
        return None
    if not key or "../" in key or key.startswith("/"):
        logger.error(json.dumps({"message": "Invalid key", "key": key}))
        return None

    if not is_supported_extension(key):
        logger.warning(
            json.dumps(
                {
                    "message": "Unsupported file extension",
                    "bucket": bucket,
                    "key": key,
                    "extension": os.path.splitext(key)[1],
                }
            )
        )
        return None

    try:
        region = os.environ.get("AWS_REGION", "eu-west-1")
        s3 = boto3.client("s3", region_name=region)
        response = s3.get_object(Bucket=bucket, Key=key)
        # Validate file size to prevent memory exhaustion (max 10MB)
        content_length = response.get("ContentLength", 0)
        max_size = 10_000_000
        if content_length > max_size:
            logger.error(json.dumps({
                "message": "S3 object too large",
                "bucket": bucket,
                "key": key,
                "content_length": content_length,
                "max_size": max_size,
            }))
            return None
        return response["Body"].read(max_size).decode("utf-8")
    except Exception as exc:
        error_code = ""
        if hasattr(exc, "response"):
            error_code = exc.response.get("Error", {}).get("Code", "")
        logger.error(json.dumps({
            "message": "S3 read failed",
            "bucket": bucket,
            "key": key,
            "error_code": error_code,
            "error": str(exc),
        }))
        return None
