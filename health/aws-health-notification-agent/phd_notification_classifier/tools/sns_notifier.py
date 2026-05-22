# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""SNS Notifier tool.

Publishes structured notification summaries to a configured SNS topic
after classification and impact analysis. Reads the topic ARN from the
SNS_TOPIC_ARN environment variable.
"""

from __future__ import annotations

import json
import logging
import os

import boto3
from strands import tool

logger = logging.getLogger(__name__)

SNS_PAYLOAD_FIELDS = (
    "notification_id",
    "event_type",
    "affected_service",
    "classification",
    "reason",
    "affected_accounts",
    "impact_analysis",
    "cost_projection",
)


@tool
def publish_to_sns(notification_summary: dict) -> dict:
    """Publish a structured notification summary to the configured SNS topic.

    Reads SNS_TOPIC_ARN from environment variable. Includes classification,
    impact analysis, cost projections, and affected accounts as structured JSON.
    Returns publish result or failure details.

    Args:
        notification_summary: A dict containing classification results with
            notification_id, event_type, affected_service, classification,
            reason, impact_analysis, cost_projection, and affected_accounts.

    Returns:
        A dict with status and either message_id, skip reason, or error.
    """
    topic_arn = os.environ.get("SNS_TOPIC_ARN", "")
    logger.info("SNS_TOPIC_ARN resolved to: %s", topic_arn[:20] + "..." if len(topic_arn) > 20 else topic_arn)
    if not topic_arn:
        logger.warning("SNS_TOPIC_ARN not configured — skipping SNS publish")
        return {"status": "skipped", "reason": "SNS_TOPIC_ARN not configured"}

    payload = {field: notification_summary.get(field) for field in SNS_PAYLOAD_FIELDS}

    try:
        region = topic_arn.split(":")[3]
        client = boto3.client("sns", region_name=region)
        response = client.publish(
            TopicArn=topic_arn,
            Message=json.dumps(payload),
            Subject="AWS Health Notification Classification",
        )
        message_id = response.get("MessageId", "")
        return {"status": "sent", "message_id": message_id}
    except Exception as exc:
        logger.error("Failed to publish to SNS: %s", exc)
        return {"status": "failed", "error": str(exc)}
