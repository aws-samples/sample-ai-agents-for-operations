# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Unit tests for sns_publisher.publish_to_sns()."""

import json
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from aha_eventbridge_lambda.sns_publisher import publish_to_sns


def _parsed_event(**overrides):
    """Build a minimal parsed health event dict."""
    base = {
        "event_arn": "arn:aws:health:us-east-1::event/RDS/AWS_RDS_MAINTENANCE/123",
        "status_code": "upcoming",
        "affected_accounts": ["111111111111", "222222222222"],
        "event_type_category": "scheduledChange",
        "event_description": "Scheduled maintenance for your RDS instance.",
        "service": "RDS",
    }
    base.update(overrides)
    return base


TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:health-notifications"


class TestPublishToSns:
    """Tests for publish_to_sns() covering message format, subject, and errors."""

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_message_body_is_json_with_all_fields(self, mock_client):
        """Req 5.1, 5.2: Message body is JSON with all required fields."""
        mock_client.publish.return_value = {"MessageId": "msg-123"}
        event = _parsed_event()

        publish_to_sns(event, TOPIC_ARN)

        call_kwargs = mock_client.publish.call_args[1]
        body = json.loads(call_kwargs["Message"])

        assert body["event_arn"] == event["event_arn"]
        assert body["event_type_category"] == event["event_type_category"]
        assert body["affected_accounts"] == event["affected_accounts"]
        assert body["event_description"] == event["event_description"]
        assert body["service"] == event["service"]
        assert body["status_code"] == event["status_code"]

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_subject_format(self, mock_client):
        """Req 5.3: Subject is '{event_type_category}: {service}'."""
        mock_client.publish.return_value = {"MessageId": "msg-123"}
        event = _parsed_event()

        publish_to_sns(event, TOPIC_ARN)

        call_kwargs = mock_client.publish.call_args[1]
        assert call_kwargs["Subject"] == "scheduledChange: RDS"

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_topic_arn_passed_to_publish(self, mock_client):
        """Req 5.1: Topic ARN is forwarded to the SNS publish call."""
        mock_client.publish.return_value = {"MessageId": "msg-123"}

        publish_to_sns(_parsed_event(), TOPIC_ARN)

        call_kwargs = mock_client.publish.call_args[1]
        assert call_kwargs["TopicArn"] == TOPIC_ARN

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_returns_sns_response(self, mock_client):
        """publish_to_sns returns the SNS publish response dict."""
        expected = {"MessageId": "msg-456"}
        mock_client.publish.return_value = expected

        result = publish_to_sns(_parsed_event(), TOPIC_ARN)

        assert result == expected

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_publish_failure_raises_and_logs(self, mock_client, caplog):
        """Req 5.4: SNS publish failure logs details and raises."""
        mock_client.publish.side_effect = ClientError(
            {"Error": {"Code": "AuthorizationError", "Message": "not authorized"},
             "ResponseMetadata": {"HTTPStatusCode": 403}},
            "Publish",
        )

        with pytest.raises(ClientError):
            publish_to_sns(_parsed_event(), TOPIC_ARN)

        log_messages = [r.message for r in caplog.records]
        failure_logs = [m for m in log_messages if "Failed to publish" in m]
        assert len(failure_logs) == 1
        log_data = json.loads(failure_logs[0])
        assert log_data["error_type"] == "ClientError"
        assert "not authorized" in log_data["error_message"]

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_empty_affected_accounts(self, mock_client):
        """Edge case: empty affected accounts list is included in message."""
        mock_client.publish.return_value = {"MessageId": "msg-789"}
        event = _parsed_event(affected_accounts=[])

        publish_to_sns(event, TOPIC_ARN)

        call_kwargs = mock_client.publish.call_args[1]
        body = json.loads(call_kwargs["Message"])
        assert body["affected_accounts"] == []

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_empty_event_description(self, mock_client):
        """Edge case: empty event description is included in message."""
        mock_client.publish.return_value = {"MessageId": "msg-000"}
        event = _parsed_event(event_description="")

        publish_to_sns(event, TOPIC_ARN)

        call_kwargs = mock_client.publish.call_args[1]
        body = json.loads(call_kwargs["Message"])
        assert body["event_description"] == ""

    @patch("aha_eventbridge_lambda.sns_publisher._client")
    def test_account_notification_subject(self, mock_client):
        """Req 5.3: Subject works for accountNotification category."""
        mock_client.publish.return_value = {"MessageId": "msg-111"}
        event = _parsed_event(
            event_type_category="accountNotification",
            service="BILLING",
        )

        publish_to_sns(event, TOPIC_ARN)

        call_kwargs = mock_client.publish.call_args[1]
        assert call_kwargs["Subject"] == "accountNotification: BILLING"
