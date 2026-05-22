# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Unit tests for the SNS Notifier tool."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from phd_notification_classifier.tools.sns_notifier import publish_to_sns, SNS_PAYLOAD_FIELDS


_SAMPLE_SUMMARY = {
    "notification_id": "arn:aws:health:us-east-1::event/EKS/123",
    "event_type": "AWS_EKS_PLANNED_LIFECYCLE_EVENT",
    "affected_service": "EKS",
    "classification": "COST_IMPLICATION",
    "reason": "EKS extended support incurs additional charges.",
    "affected_accounts": [
        {
            "account_id": "111111111111",
            "account_name": "dev-account",
            "environment_type": "non-production",
            "affected_resources": [],
        }
    ],
    "impact_analysis": None,
    "cost_projection": {"projectable": True, "org_total_projected_cost": 100.0},
}


class TestPublishSuccess:
    """Successful SNS publish."""

    @patch.dict("os.environ", {"SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic"})
    @patch("phd_notification_classifier.tools.sns_notifier.boto3")
    def test_returns_sent_with_message_id(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.publish.return_value = {"MessageId": "msg-abc-123"}
        mock_boto3.client.return_value = mock_client

        result = publish_to_sns(notification_summary=_SAMPLE_SUMMARY)

        assert result["status"] == "sent"
        assert result["message_id"] == "msg-abc-123"

    @patch.dict("os.environ", {"SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic"})
    @patch("phd_notification_classifier.tools.sns_notifier.boto3")
    def test_publish_called_with_structured_json(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.publish.return_value = {"MessageId": "msg-1"}
        mock_boto3.client.return_value = mock_client

        publish_to_sns(notification_summary=_SAMPLE_SUMMARY)

        call_kwargs = mock_client.publish.call_args[1]
        assert call_kwargs["TopicArn"] == "arn:aws:sns:us-east-1:123456789012:topic"
        message = json.loads(call_kwargs["Message"])
        for field in SNS_PAYLOAD_FIELDS:
            assert field in message


class TestMissingTopicArn:
    """SNS_TOPIC_ARN not set."""

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_skipped(self):
        result = publish_to_sns(notification_summary=_SAMPLE_SUMMARY)

        assert result["status"] == "skipped"
        assert "SNS_TOPIC_ARN not configured" in result["reason"]

    @patch.dict("os.environ", {}, clear=True)
    def test_logs_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            publish_to_sns(notification_summary=_SAMPLE_SUMMARY)

        assert any("SNS_TOPIC_ARN" in r.message for r in caplog.records)


class TestPublishFailure:
    """SNS publish raises an exception."""

    @patch.dict("os.environ", {"SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic"})
    @patch("phd_notification_classifier.tools.sns_notifier.boto3")
    def test_returns_failed_with_error(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.publish.side_effect = Exception("Access denied")
        mock_boto3.client.return_value = mock_client

        result = publish_to_sns(notification_summary=_SAMPLE_SUMMARY)

        assert result["status"] == "failed"
        assert "Access denied" in result["error"]

    @patch.dict("os.environ", {"SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic"})
    @patch("phd_notification_classifier.tools.sns_notifier.boto3")
    def test_logs_error(self, mock_boto3, caplog):
        mock_client = MagicMock()
        mock_client.publish.side_effect = Exception("Network error")
        mock_boto3.client.return_value = mock_client

        with caplog.at_level(logging.ERROR):
            publish_to_sns(notification_summary=_SAMPLE_SUMMARY)

        assert any("Failed to publish" in r.message for r in caplog.records)


class TestPayloadStructure:
    """Verify SNS message contains all required fields."""

    @patch.dict("os.environ", {"SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:topic"})
    @patch("phd_notification_classifier.tools.sns_notifier.boto3")
    def test_payload_contains_all_required_fields(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.publish.return_value = {"MessageId": "msg-1"}
        mock_boto3.client.return_value = mock_client

        publish_to_sns(notification_summary=_SAMPLE_SUMMARY)

        call_kwargs = mock_client.publish.call_args[1]
        message = json.loads(call_kwargs["Message"])

        assert message["notification_id"] == _SAMPLE_SUMMARY["notification_id"]
        assert message["event_type"] == _SAMPLE_SUMMARY["event_type"]
        assert message["affected_service"] == _SAMPLE_SUMMARY["affected_service"]
        assert message["classification"] == _SAMPLE_SUMMARY["classification"]
        assert message["reason"] == _SAMPLE_SUMMARY["reason"]
        assert message["affected_accounts"] == _SAMPLE_SUMMARY["affected_accounts"]
        assert message["impact_analysis"] == _SAMPLE_SUMMARY["impact_analysis"]
        assert message["cost_projection"] == _SAMPLE_SUMMARY["cost_projection"]
