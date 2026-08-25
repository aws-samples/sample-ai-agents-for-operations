"""Layer 5: Human-in-the-loop review gates for MIO Agent.

Implements the human-in-the-loop review gate:
- An authorized reviewer must approve customer-facing reports before delivery
- Audit trail of who approved what and when
- Auto-expiry for stale approvals
- Feedback mechanism for finding accuracy

This is the single most important guardrail for ensuring
reports are reviewed before reaching customers.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

_BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "adaptive"})

REVIEW_TABLE = "mio-agent-reviews"
FEEDBACK_TABLE = "mio-agent-feedback"

# Reports expire for review after this many hours
REVIEW_EXPIRY_HOURS = 48


class ReviewStatus(str, Enum):
    """Human review status for a generated report."""

    PENDING_REVIEW = "PENDING_REVIEW"    # Generated, awaiting TAM review
    APPROVED = "APPROVED"                # TAM reviewed and approved
    REJECTED = "REJECTED"                # TAM rejected — needs regeneration
    EXPIRED = "EXPIRED"                  # Not reviewed within 48 hours
    AUTO_APPROVED = "AUTO_APPROVED"      # TAM brief — auto-approved (informational only)


class ReportAudience(str, Enum):
    """Report audience determines review requirements."""
    TAM = "tam"                  # Informational — auto-approved
    CUSTOMER = "customer"        # Requires TAM approval before sharing
    LEADERSHIP = "leadership"    # Requires TAM approval before sharing


# Which audiences require explicit human approval
REQUIRES_APPROVAL = {ReportAudience.CUSTOMER, ReportAudience.LEADERSHIP}


class ReviewRecord:
    """A review record for a generated report."""

    def __init__(
        self,
        report_id: str,
        assessment_id: str,
        account_id: str,
        account_name: str,
        audience: ReportAudience,
        report_type: str,
        generated_by: str,
        region: str = "us-east-1",
    ) -> None:
        self.report_id = report_id
        self.assessment_id = assessment_id
        self.account_id = account_id
        self.account_name = account_name
        self.audience = audience
        self.report_type = report_type
        self.generated_by = generated_by
        self.region = region
        self.created_at = datetime.utcnow()
        self.expires_at = self.created_at + timedelta(hours=REVIEW_EXPIRY_HOURS)

        # Auto-approve TAM briefs (informational, TAM reads before using)
        if audience == ReportAudience.TAM:
            self.status = ReviewStatus.AUTO_APPROVED
            self.reviewed_by = "auto"
            self.reviewed_at: datetime | None = self.created_at
            self.review_notes: str | None = "TAM briefs are informational — TAM reviews before use"
        else:
            self.status = ReviewStatus.PENDING_REVIEW
            self.reviewed_by = None
            self.reviewed_at = None
            self.review_notes = None


def create_review_record(
    report_id: str,
    assessment_id: str,
    account_id: str,
    account_name: str,
    audience: ReportAudience,
    report_type: str,
    generated_by: str,
    region: str = "us-east-1",
) -> ReviewRecord:
    """Create and persist a review record for a generated report.

    Args:
        report_id: Unique report identifier.
        assessment_id: Source assessment ID.
        account_id: AWS account ID.
        account_name: Customer account name.
        audience: Target audience (determines review requirement).
        report_type: Type of report (tam_brief, customer_report, etc.).
        generated_by: TAM alias or system identifier.
        region: AWS region.

    Returns:
        Created ReviewRecord.
    """
    record = ReviewRecord(
        report_id=report_id,
        assessment_id=assessment_id,
        account_id=account_id,
        account_name=account_name,
        audience=audience,
        report_type=report_type,
        generated_by=generated_by,
        region=region,
    )

    _persist_review_record(record, region)

    if record.status == ReviewStatus.PENDING_REVIEW:
        logger.info(
            "Report pending human review",
            extra={
                "report_id": report_id,
                "account_id": account_id,
                "audience": audience.value,
                "expires_at": record.expires_at.isoformat(),
            },
        )
    else:
        logger.info(
            "Report auto-approved (TAM brief)",
            extra={"report_id": report_id, "account_id": account_id},
        )

    return record


def approve_report(
    report_id: str,
    reviewed_by: str,
    review_notes: str | None = None,
    region: str = "us-east-1",
) -> ReviewRecord | None:
    """TAM approves a report for customer/leadership delivery.

    Args:
        report_id: Report ID to approve.
        reviewed_by: TAM alias approving the report.
        review_notes: Optional review notes.
        region: AWS region.

    Returns:
        Updated ReviewRecord or None if not found/expired.
    """
    record = get_review_record(report_id, region)
    if not record:
        logger.warning("Review record not found", extra={"report_id": report_id})
        return None

    if record.status == ReviewStatus.EXPIRED:
        logger.warning("Cannot approve expired review", extra={"report_id": report_id})
        return record

    if datetime.utcnow() > record.expires_at:
        record.status = ReviewStatus.EXPIRED
        _update_review_status(record, region)
        return record

    record.status = ReviewStatus.APPROVED
    record.reviewed_by = reviewed_by
    record.reviewed_at = datetime.utcnow()
    record.review_notes = review_notes

    _update_review_status(record, region)

    logger.info(
        "Report approved for delivery",
        extra={
            "report_id": report_id,
            "reviewed_by": reviewed_by,
            "account_id": record.account_id,
        },
    )
    return record


def reject_report(
    report_id: str,
    reviewed_by: str,
    rejection_reason: str,
    region: str = "us-east-1",
) -> ReviewRecord | None:
    """TAM rejects a report — triggers regeneration with feedback.

    Args:
        report_id: Report ID to reject.
        reviewed_by: TAM alias rejecting the report.
        rejection_reason: Reason for rejection (fed back to agent).
        region: AWS region.

    Returns:
        Updated ReviewRecord.
    """
    record = get_review_record(report_id, region)
    if not record:
        return None

    record.status = ReviewStatus.REJECTED
    record.reviewed_by = reviewed_by
    record.reviewed_at = datetime.utcnow()
    record.review_notes = rejection_reason

    _update_review_status(record, region)

    logger.info(
        "Report rejected",
        extra={
            "report_id": report_id,
            "reviewed_by": reviewed_by,
            "reason": rejection_reason[:100],
        },
    )
    return record


def record_finding_feedback(
    finding_id: str,
    assessment_id: str,
    account_id: str,
    is_accurate: bool,
    tam_alias: str,
    notes: str | None = None,
    region: str = "us-east-1",
) -> None:
    """TAM provides feedback on finding accuracy.

    This feeds the quality improvement loop. If >20% of findings
    in a dimension are marked inaccurate, a calibration alert is triggered.

    Args:
        finding_id: Finding ID being rated.
        assessment_id: Assessment that produced this finding.
        account_id: AWS account ID.
        is_accurate: Whether the finding was accurate.
        tam_alias: TAM providing feedback.
        notes: Optional explanation.
        region: AWS region.
    """
    client = boto3.client("dynamodb", region_name=region, config=_BOTO_CONFIG)

    item = {
        "finding_id": {"S": finding_id},
        "feedback_timestamp": {"S": datetime.utcnow().isoformat()},
        "assessment_id": {"S": assessment_id},
        "account_id": {"S": account_id},
        "is_accurate": {"BOOL": is_accurate},
        "tam_alias": {"S": tam_alias},
        "ttl": {"N": str(int(datetime.utcnow().timestamp() + 365 * 24 * 3600))},
    }
    if notes:
        item["notes"] = {"S": notes}

    try:
        client.put_item(TableName=FEEDBACK_TABLE, Item=item)
        logger.info(
            "Finding feedback recorded",
            extra={
                "finding_id": finding_id,
                "is_accurate": is_accurate,
                "tam_alias": tam_alias,
            },
        )
    except ClientError as e:
        logger.error("Failed to record finding feedback", extra={"error": str(e)})


def get_review_record(report_id: str, region: str = "us-east-1") -> ReviewRecord | None:
    """Retrieve a review record by report ID."""
    client = boto3.client("dynamodb", region_name=region, config=_BOTO_CONFIG)
    try:
        response = client.get_item(
            TableName=REVIEW_TABLE,
            Key={"report_id": {"S": report_id}},
        )
        item = response.get("Item")
        if not item:
            return None
        return _item_to_record(item)
    except ClientError as e:
        logger.error("Failed to get review record", extra={"error": str(e)})
        return None


def check_delivery_authorized(report_id: str, region: str = "us-east-1") -> tuple[bool, str]:
    """Check whether a report is authorized for delivery.

    Args:
        report_id: Report ID to check.
        region: AWS region.

    Returns:
        Tuple of (is_authorized, reason).
    """
    record = get_review_record(report_id, region)
    if not record:
        return False, "Review record not found"

    if record.status == ReviewStatus.APPROVED:
        return True, f"Approved by {record.reviewed_by}"
    elif record.status == ReviewStatus.AUTO_APPROVED:
        return True, "TAM brief — informational, no approval required"
    elif record.status == ReviewStatus.PENDING_REVIEW:
        # Check if expired
        if datetime.utcnow() > record.expires_at:
            return False, f"Review expired at {record.expires_at.isoformat()} — regenerate assessment"
        hours_remaining = (record.expires_at - datetime.utcnow()).seconds // 3600
        return False, f"Awaiting TAM approval — expires in ~{hours_remaining} hours"
    elif record.status == ReviewStatus.REJECTED:
        return False, f"Report rejected by {record.reviewed_by}: {record.review_notes}"
    elif record.status == ReviewStatus.EXPIRED:
        return False, "Review window expired — regenerate assessment"
    else:
        return False, f"Unknown review status: {record.status}"


def _persist_review_record(record: ReviewRecord, region: str) -> None:
    """Persist a review record to DynamoDB."""
    client = boto3.client("dynamodb", region_name=region, config=_BOTO_CONFIG)
    item: dict[str, Any] = {
        "report_id": {"S": record.report_id},
        "assessment_id": {"S": record.assessment_id},
        "account_id": {"S": record.account_id},
        "account_name": {"S": record.account_name},
        "audience": {"S": record.audience.value},
        "report_type": {"S": record.report_type},
        "generated_by": {"S": record.generated_by},
        "status": {"S": record.status.value},
        "created_at": {"S": record.created_at.isoformat()},
        "expires_at": {"S": record.expires_at.isoformat()},
        "ttl": {"N": str(int(record.expires_at.timestamp() + 7 * 24 * 3600))},
    }
    if record.reviewed_by:
        item["reviewed_by"] = {"S": record.reviewed_by}
    if record.reviewed_at:
        item["reviewed_at"] = {"S": record.reviewed_at.isoformat()}
    if record.review_notes:
        item["review_notes"] = {"S": record.review_notes}
    try:
        client.put_item(TableName=REVIEW_TABLE, Item=item)
    except ClientError as e:
        logger.error("Failed to persist review record", extra={"error": str(e)})


def _update_review_status(record: ReviewRecord, region: str) -> None:
    """Update review status in DynamoDB."""
    client = boto3.client("dynamodb", region_name=region, config=_BOTO_CONFIG)
    update_expr = "SET #s = :s, reviewed_by = :rb, reviewed_at = :ra"
    expr_values: dict[str, Any] = {
        ":s": {"S": record.status.value},
        ":rb": {"S": record.reviewed_by or ""},
        ":ra": {"S": record.reviewed_at.isoformat() if record.reviewed_at else ""},
    }
    if record.review_notes:
        update_expr += ", review_notes = :rn"
        expr_values[":rn"] = {"S": record.review_notes}
    try:
        client.update_item(
            TableName=REVIEW_TABLE,
            Key={"report_id": {"S": record.report_id}},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=expr_values,
        )
    except ClientError as e:
        logger.error("Failed to update review record", extra={"error": str(e)})


def _item_to_record(item: dict[str, Any]) -> ReviewRecord:
    """Convert DynamoDB item to ReviewRecord."""
    record = ReviewRecord.__new__(ReviewRecord)
    record.report_id = item["report_id"]["S"]
    record.assessment_id = item["assessment_id"]["S"]
    record.account_id = item["account_id"]["S"]
    record.account_name = item["account_name"]["S"]
    record.audience = ReportAudience(item["audience"]["S"])
    record.report_type = item["report_type"]["S"]
    record.generated_by = item["generated_by"]["S"]
    record.status = ReviewStatus(item["status"]["S"])
    record.created_at = datetime.fromisoformat(item["created_at"]["S"])
    record.expires_at = datetime.fromisoformat(item["expires_at"]["S"])
    record.reviewed_by = item.get("reviewed_by", {}).get("S")
    record.reviewed_at = (
        datetime.fromisoformat(item["reviewed_at"]["S"])
        if item.get("reviewed_at", {}).get("S")
        else None
    )
    record.review_notes = item.get("review_notes", {}).get("S")
    record.region = "us-east-1"
    return record
