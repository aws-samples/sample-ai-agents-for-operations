"""AWS Health event trigger handler for MIO Agent."""

from __future__ import annotations

from typing import Any

import boto3

from mio_agent.models.assessment import AccessTier, AssessmentRequest, OutputAudience, TriggerType
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

SQS_QUEUE_URL_PARAM = "/mio-agent/sqs/assessment-queue-url"


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for AWS Health events from EventBridge.

    Args:
        event: EventBridge event with AWS Health detail.
        context: Lambda context.

    Returns:
        Handler response.
    """
    logger.info("Health event handler triggered")

    detail = event.get("detail", {})
    event_type_code = detail.get("eventTypeCode", "")
    affected_accounts: list[str] = detail.get("affectedAccounts", [])
    service = detail.get("service", "")

    if not affected_accounts:
        logger.warning("No affected accounts in health event")
        return {"statusCode": 200, "body": "No affected accounts"}

    enqueued = 0
    for account_id in affected_accounts:
        request = AssessmentRequest(
            account_id=account_id,
            account_name=f"Account {account_id}",
            access_tier=AccessTier.TIER1,
            trigger_type=TriggerType.HEALTH_EVENT,
            requested_by="mio-agent-automation",
            trigger_context={
                "event_type_code": event_type_code,
                "service": service,
                "health_event": str(detail)[:500],
            },
            output_audience=[OutputAudience.TAM],
        )
        _enqueue_assessment(request)
        enqueued += 1

    logger.info("Health event assessments enqueued", extra={"count": enqueued})
    return {"statusCode": 200, "body": f"Enqueued assessments for {enqueued} accounts"}


def _enqueue_assessment(request: AssessmentRequest) -> None:
    """Send assessment request to SQS."""
    ssm = boto3.client("ssm")
    queue_url = ssm.get_parameter(Name=SQS_QUEUE_URL_PARAM)["Parameter"]["Value"]
    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=request.model_dump_json(),
        MessageAttributes={
            "trigger_type": {"StringValue": "health_event", "DataType": "String"},
        },
    )
