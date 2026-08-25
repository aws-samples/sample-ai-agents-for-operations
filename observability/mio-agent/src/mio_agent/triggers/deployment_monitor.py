"""Deployment monitor trigger — detects new resource deployments via CloudTrail events."""

from __future__ import annotations

import json
from typing import Any

import boto3

from mio_agent.models.assessment import AccessTier, AssessmentRequest, OutputAudience, TriggerType
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

SQS_QUEUE_URL_PARAM = "/mio-agent/sqs/assessment-queue-url"

# CloudTrail event names that indicate new resource creation
MONITORED_EVENTS = {
    "RunInstances",           # EC2
    "CreateFunction20150331", # Lambda
    "CreateDBInstance",       # RDS
    "CreateCluster",          # ECS/EKS
    "CreateRestApi",          # API Gateway
    "CreateStack",            # CloudFormation
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for CloudTrail deployment events from EventBridge.

    Triggered when new AWS resources are created. Checks if monitoring
    was provisioned alongside the new resources.

    Args:
        event: EventBridge event with CloudTrail detail.
        context: Lambda context.

    Returns:
        Handler response.
    """
    logger.info("Deployment monitor triggered")

    detail = event.get("detail", {})
    event_name = detail.get("eventName", "")
    account_id = detail.get("recipientAccountId", detail.get("userIdentity", {}).get("accountId", ""))

    if not account_id:
        logger.warning("No account ID in deployment event")
        return {"statusCode": 400, "body": "Missing account ID"}

    if event_name not in MONITORED_EVENTS:
        return {"statusCode": 200, "body": f"Event {event_name} not monitored"}

    resource_info = _extract_resource_info(detail)
    logger.info(
        "New resource deployment detected",
        extra={"account_id": account_id, "event_name": event_name},
    )

    request = AssessmentRequest(
        account_id=account_id,
        account_name=f"Account {account_id}",
        access_tier=AccessTier.TIER1,
        trigger_type=TriggerType.DEPLOYMENT,
        requested_by="mio-agent-automation",
        trigger_context={
            "event_name": event_name,
            "resource_info": resource_info,
        },
        output_audience=[OutputAudience.TAM],
    )

    _enqueue_assessment(request)
    return {"statusCode": 200, "body": "Assessment enqueued for new deployment"}


def _extract_resource_info(detail: dict[str, Any]) -> dict[str, Any]:
    """Extract resource information from CloudTrail event detail."""
    return {
        "event_name": detail.get("eventName"),
        "event_source": detail.get("eventSource"),
        "aws_region": detail.get("awsRegion"),
        "request_parameters": str(detail.get("requestParameters", {}))[:500],
    }


def _enqueue_assessment(request: AssessmentRequest) -> None:
    """Send an assessment request to SQS."""
    ssm = boto3.client("ssm")
    queue_url = ssm.get_parameter(Name=SQS_QUEUE_URL_PARAM)["Parameter"]["Value"]
    sqs = boto3.client("sqs")
    sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=request.model_dump_json(),
        MessageAttributes={
            "trigger_type": {"StringValue": "deployment", "DataType": "String"},
        },
    )
