# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Property-based tests for sns_publisher.publish_to_sns().

Uses Hypothesis to verify universal properties across randomly generated inputs.
"""

import json
from unittest.mock import MagicMock, patch

from hypothesis import given, settings
from hypothesis import strategies as st

from aha_eventbridge_lambda.sns_publisher import publish_to_sns


# --- Strategies ---

aws_account_id = st.from_regex(r"[0-9]{12}", fullmatch=True)

event_arn = st.from_regex(
    r"arn:aws:health:[a-z]{2}-[a-z]+-[0-9]::[a-z]+/[A-Z0-9_]+/[A-Z0-9_]+/[a-f0-9]+",
    fullmatch=True,
)

sns_category = st.sampled_from(["scheduledChange", "accountNotification"])

service_name = st.sampled_from(["EC2", "RDS", "LAMBDA", "S3", "ECS", "EKS", "DYNAMODB"])

status_code = st.sampled_from(["open", "closed", "upcoming"])

description_text = st.text(
    min_size=1, max_size=500, alphabet=st.characters(blacklist_categories=("Cs",))
)

topic_arn = st.from_regex(
    r"arn:aws:sns:[a-z]{2}-[a-z]+-[0-9]:[0-9]{12}:[A-Za-z0-9_-]+",
    fullmatch=True,
)


def make_parsed_event(arn, svc, cat, status, desc, accounts):
    """Build a parsed health event dict matching the output of parse_health_event()."""
    return {
        "event_arn": arn,
        "service": svc,
        "event_type_category": cat,
        "status_code": status,
        "event_description": desc,
        "affected_accounts": accounts,
    }


# Feature: aha-eventbridge-lambda, Property 7: SNS message contains all required fields as JSON
# **Validates: Requirements 5.1, 5.2, 5.3**
class TestProperty7SnsMessageCompleteness:
    """Property 7: SNS message contains all required fields as JSON.

    For any health event published to SNS, the message body shall be a valid
    JSON object containing the event ARN, event type category, affected
    accounts, event description, service, and status code. The message subject
    shall contain both the event type category and the service name.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=sns_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
        t_arn=topic_arn,
    )
    @settings(max_examples=100)
    def test_message_body_is_valid_json_with_all_required_fields(
        self, arn, svc, cat, status, desc, accounts, t_arn
    ):
        """The Message kwarg passed to _client.publish() is valid JSON containing
        event_arn, event_type_category, affected_accounts, event_description,
        service, and status_code with correct values."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)

        mock_client = MagicMock()
        mock_client.publish.return_value = {"MessageId": "test-msg-id"}

        with patch("aha_eventbridge_lambda.sns_publisher._client", mock_client):
            publish_to_sns(parsed, t_arn)

        mock_client.publish.assert_called_once()
        call_kwargs = mock_client.publish.call_args[1]

        # Message body must be valid JSON
        message_body = json.loads(call_kwargs["Message"])

        # All required fields must be present with correct values
        assert message_body["event_arn"] == arn, "event_arn mismatch"
        assert message_body["event_type_category"] == cat, "event_type_category mismatch"
        assert message_body["affected_accounts"] == accounts, "affected_accounts mismatch"
        assert message_body["event_description"] == desc, "event_description mismatch"
        assert message_body["service"] == svc, "service mismatch"
        assert message_body["status_code"] == status, "status_code mismatch"

    @given(
        arn=event_arn,
        svc=service_name,
        cat=sns_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
        t_arn=topic_arn,
    )
    @settings(max_examples=100)
    def test_subject_contains_category_and_service(
        self, arn, svc, cat, status, desc, accounts, t_arn
    ):
        """The Subject kwarg passed to _client.publish() contains the event type
        category and the service name in the format '{category}: {service}'."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)

        mock_client = MagicMock()
        mock_client.publish.return_value = {"MessageId": "test-msg-id"}

        with patch("aha_eventbridge_lambda.sns_publisher._client", mock_client):
            publish_to_sns(parsed, t_arn)

        mock_client.publish.assert_called_once()
        call_kwargs = mock_client.publish.call_args[1]

        subject = call_kwargs["Subject"]
        assert cat in subject, f"event_type_category '{cat}' not found in subject '{subject}'"
        assert svc in subject, f"service '{svc}' not found in subject '{subject}'"
        assert subject == f"{cat}: {svc}", (
            f"Expected subject format '{{category}}: {{service}}', got '{subject}'"
        )
