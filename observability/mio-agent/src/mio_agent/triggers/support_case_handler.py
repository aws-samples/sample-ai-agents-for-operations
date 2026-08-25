"""Support case event trigger handler for MIO Agent."""

from __future__ import annotations

import json
from typing import Any

import boto3

from mio_agent.models.assessment import AccessTier, AssessmentRequest, OutputAudience, TriggerType
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

SQS_QUEUE_URL_PARAM = "/mio-agent/sqs/assessment-queue-url"


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for support case events from SNS/EventBridge.

    Triggered when a customer opens a P1 or P2 support case.
    Parses the event, looks up the customer account, and enqueues
    an assessment request.

    Args:
        event: SNS or EventBridge event payload.
        context: Lambda context object.

    Returns:
        Handler response dict.
    """
    logger.info("Support case handler triggered", extra={"event_keys": list(event.keys())})

    case_id, account_id, severity = _parse_support_event(event)
    if not account_id:
        logger.warning("Could not extract account ID from support case event")
        return {"statusCode": 400, "body": "Missing account ID"}

    request = AssessmentRequest(
        account_id=account_id,
        account_name=f"Account {account_id}",
        access_tier=AccessTier.TIER1,  # Upgrades to tier3 if role is configured
        trigger_type=TriggerType.SUPPORT_CASE,
        requested_by="mio-agent-automation",
        trigger_context={
            "support_case_id": case_id,
            "severity": severity,
        },
        output_audience=[OutputAudience.TAM],
    )

    _enqueue_assessment(request)
    logger.info(
        "Assessment enqueued for support case",
        extra={"account_id": account_id, "case_id": case_id},
    )
    return {"statusCode": 200, "body": f"Assessment enqueued for account {account_id}"}


def _parse_support_event(event: dict[str, Any]) -> tuple[str, str, str]:
    """Extract case ID, account ID, and severity from the event."""
    # Handle SNS wrapper
    if "Records" in event:
        record = event["Records"][0]
        if record.get("EventSource") == "aws:sns":
            message = json.loads(record["Sns"]["Message"])
            return (
                message.get("case_id", ""),
                message.get("account_id", ""),
                message.get("severity", "unknown"),
            )

    # Direct EventBridge event
    detail = event.get("detail", event)
    return (
        detail.get("case_id", detail.get("caseId", "")),
        detail.get("account_id", detail.get("accountId", "")),
        detail.get("severity", detail.get("severityCode", "unknown")),
    )


def _enqueue_assessment(request: AssessmentRequest) -> None:
    """Send an assessment request to the SQS queue."""
    ssm = boto3.client("ssm")
    queue_url = ssm.get_parameter(Name=SQS_QUEUE_URL_PARAM)["Parameter"]["Value"]

    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=request.model_dump_json(),
        MessageAttributes={
            "trigger_type": {"StringValue": "support_case", "DataType": "String"},
        },
    )
