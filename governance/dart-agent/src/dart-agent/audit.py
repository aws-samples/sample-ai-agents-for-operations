# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Audit trail writer.

Persists one tamper-evident record per DART run to the DynamoDB audit table
(agent-managed). Supports the EU AI Act Article 10 auditability described in
the README: who/what/when, the request, the recommendation, and finding counts.

The table schema (see infra/lib/DartAgentStack.ts):
  - partition key: run_id (S)
  - sort key:      timestamp (S)
  - TTL attribute: ttl (N)

Writes are best-effort: a failure to persist audit data is logged but does not
fail the run (the analysis result is still returned to the caller).
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from config import Config

logger = logging.getLogger(__name__)

# Retain audit records for one year by default (configurable).
_AUDIT_TTL_DAYS = int(__import__("os").environ.get("AUDIT_TTL_DAYS", "365"))


def write_audit_record(
    run_id: str,
    message: str,
    recommendation: str | None = None,
    quality_score: int | None = None,
    findings_count: dict[str, int] | None = None,
    status: str = "complete",
) -> bool:
    """
    Write a single audit record for a DART run to the DynamoDB audit table.

    Args:
        run_id: Unique run identifier (partition key).
        message: The user request (stored truncated; may reference a dataset path).
        recommendation: Final GO / NO-GO / GO WITH FIXES, if available.
        quality_score: 0–100 quality score, if available.
        findings_count: Severity → count map, if available.
        status: Run status (e.g. "complete", "error").

    Returns:
        True if the record was written, False on failure (logged, non-fatal).
    """
    if not Config.AUDIT_TABLE:
        logger.warning(json.dumps({
            "event": "audit_skipped",
            "reason": "AUDIT_TABLE not configured",
            "run_id": run_id,
        }))
        return False

    now = datetime.now(timezone.utc)
    ttl = int(time.time()) + _AUDIT_TTL_DAYS * 86400

    item: dict[str, Any] = {
        "run_id": {"S": run_id},
        "timestamp": {"S": now.isoformat()},
        "status": {"S": status},
        "message_preview": {"S": (message or "")[:1024]},
        "ttl": {"N": str(ttl)},
    }
    if recommendation is not None:
        item["recommendation"] = {"S": recommendation}
    if quality_score is not None:
        item["quality_score"] = {"N": str(int(quality_score))}
    if findings_count is not None:
        item["findings_count"] = {"S": json.dumps(findings_count)}

    try:
        client = boto3.client("dynamodb", region_name=Config.AWS_REGION)
        client.put_item(TableName=Config.AUDIT_TABLE, Item=item)
        logger.info(json.dumps({
            "event": "audit_written",
            "run_id": run_id,
            "table": Config.AUDIT_TABLE,
            "status": status,
        }))
        return True
    except ClientError as e:
        logger.warning(json.dumps({
            "event": "audit_write_error",
            "run_id": run_id,
            "error": str(e),
        }))
        return False
