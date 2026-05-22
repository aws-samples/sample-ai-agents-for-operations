# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Publish AWS Health event summaries to an SNS topic."""

import json
import logging

import boto3

logger = logging.getLogger(__name__)

_client = boto3.client("sns")


def publish_to_sns(parsed_event: dict, topic_arn: str) -> dict:
    """Publish a JSON summary of the health event to the SNS topic.

    Formats the message as a JSON object containing the event ARN, event type
    category, affected accounts, event description, service, and status code.
    Sets the message subject to ``"{event_type_category}: {service}"``.

    Args:
        parsed_event: Dict produced by ``parse_health_event()`` with keys
            ``event_arn``, ``event_type_category``, ``affected_accounts``,
            ``event_description``, ``service``, and ``status_code``.
        topic_arn: The SNS topic ARN (from ``SNS_TOPIC_ARN``).

    Returns:
        The SNS publish response dict.

    Raises:
        Exception: If the SNS publish operation fails.
    """
    message = json.dumps({
        "event_arn": parsed_event["event_arn"],
        "event_type_category": parsed_event["event_type_category"],
        "affected_accounts": parsed_event["affected_accounts"],
        "event_description": parsed_event["event_description"],
        "service": parsed_event["service"],
        "status_code": parsed_event["status_code"],
    })

    subject = f"{parsed_event['event_type_category']}: {parsed_event['service']}"

    try:
        response = _client.publish(
            TopicArn=topic_arn,
            Message=message,
            Subject=subject,
        )
        logger.info(
            json.dumps({
                "message": "Published event to SNS",
                "event_arn": parsed_event["event_arn"],
                "topic_arn": topic_arn,
                "message_id": response.get("MessageId", ""),
            })
        )
        return response
    except Exception as exc:
        logger.warning(
            json.dumps({
                "message": "Failed to publish to SNS",
                "event_arn": parsed_event["event_arn"],
                "topic_arn": topic_arn,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
        )
        raise

def publish_summary_to_sns(
    summary: str,
    subject: str,
    topic_arn: str,
    event_arn: str,
) -> dict:
    """Publish a Human_Readable_Summary (plain text) to the SNS topic.

    Args:
        summary: The plain-text Human_Readable_Summary.
        subject: The SNS message subject (e.g., "BREAKING_CHANGE: CASSANDRA").
        topic_arn: The SNS topic ARN.
        event_arn: The event ARN for logging context.

    Returns:
        The SNS publish response dict.

    Raises:
        Exception: If the SNS publish operation fails.
    """
    try:
        response = _client.publish(
            TopicArn=topic_arn,
            Message=summary,
            Subject=subject,
        )
        logger.info(
            json.dumps({
                "message": "Published summary to SNS",
                "event_arn": event_arn,
                "topic_arn": topic_arn,
                "message_id": response.get("MessageId", ""),
            })
        )
        return response
    except Exception as exc:
        logger.warning(
            json.dumps({
                "message": "Failed to publish summary to SNS",
                "event_arn": event_arn,
                "topic_arn": topic_arn,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
        )
        raise

