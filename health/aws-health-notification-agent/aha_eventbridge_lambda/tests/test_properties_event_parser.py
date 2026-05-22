# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Property-based tests for event_parser.parse_health_event().

Uses Hypothesis to verify universal properties across randomly generated inputs.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aha_eventbridge_lambda.event_parser import parse_health_event


# --- Strategies ---

aws_account_id = st.from_regex(r"[0-9]{12}", fullmatch=True)

event_arn = st.from_regex(
    r"arn:aws:health:[a-z]{2}-[a-z]+-[0-9]::[a-z]+/[A-Z0-9_]+/[A-Z0-9_]+/[a-f0-9]+",
    fullmatch=True,
)

category = st.sampled_from(["issue", "investigation", "scheduledChange", "accountNotification"])

service_name = st.sampled_from(["EC2", "RDS", "LAMBDA", "S3", "ECS", "EKS", "DYNAMODB"])

status_code = st.sampled_from(["open", "closed", "upcoming"])

description_text = st.text(min_size=1, max_size=500, alphabet=st.characters(blacklist_categories=("Cs",)))


def eventbridge_event(arn, svc, cat, status, desc, accounts):
    """Build a valid EventBridge health event from components."""
    return {
        "detail": {
            "eventArn": arn,
            "service": svc,
            "eventTypeCategory": cat,
            "statusCode": status,
            "eventDescription": [
                {"language": "en_US", "latestDescription": desc},
            ],
            "affectedEntities": [
                {"entityValue": f"entity-{i}", "awsAccountId": acct}
                for i, acct in enumerate(accounts)
            ],
        }
    }


# Feature: aha-eventbridge-lambda, Property 1: Event parsing extracts all required fields
# **Validates: Requirements 1.1, 1.2**
class TestProperty1EventParsingExtractsAllFields:
    """Property 1: Event parsing extracts all required fields.

    For any valid EventBridge health event payload containing an event ARN,
    status code, affected accounts, event type category, event description,
    and service name, the event parser shall extract all six fields with
    values matching the original payload.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
    )
    @settings(max_examples=100)
    def test_parser_extracts_all_six_fields(self, arn, svc, cat, status, desc, accounts):
        """Parsed output matches every field from the original payload."""
        event = eventbridge_event(arn, svc, cat, status, desc, accounts)
        result = parse_health_event(event)

        assert result["event_arn"] == arn
        assert result["status_code"] == status
        assert result["event_type_category"] == cat
        assert result["event_description"] == desc
        assert result["service"] == svc
        assert set(result["affected_accounts"]) == set(accounts)

# Feature: aha-eventbridge-lambda, Property 2: Malformed events are rejected
# **Validates: Requirements 1.3**
class TestProperty2MalformedEventsAreRejected:
    """Property 2: Malformed events are rejected.

    For any EventBridge health event payload that is missing the event ARN,
    the event type category, or both, the event parser shall raise a ValueError.
    """

    @given(
        svc=service_name,
        cat=category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
    )
    @settings(max_examples=100)
    def test_missing_event_arn_raises_value_error(self, svc, cat, status, desc, accounts):
        """Payload without eventArn raises ValueError."""
        event = eventbridge_event("placeholder", svc, cat, status, desc, accounts)
        del event["detail"]["eventArn"]

        with pytest.raises(ValueError, match="eventArn"):
            parse_health_event(event)

    @given(
        arn=event_arn,
        svc=service_name,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
    )
    @settings(max_examples=100)
    def test_missing_event_type_category_raises_value_error(self, arn, svc, status, desc, accounts):
        """Payload without eventTypeCategory raises ValueError."""
        event = eventbridge_event(arn, svc, "placeholder", status, desc, accounts)
        del event["detail"]["eventTypeCategory"]

        with pytest.raises(ValueError, match="eventTypeCategory"):
            parse_health_event(event)

    @given(
        svc=service_name,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
    )
    @settings(max_examples=100)
    def test_missing_both_fields_raises_value_error(self, svc, status, desc, accounts):
        """Payload missing both eventArn and eventTypeCategory raises ValueError."""
        event = eventbridge_event("placeholder", svc, "placeholder", status, desc, accounts)
        del event["detail"]["eventArn"]
        del event["detail"]["eventTypeCategory"]

        with pytest.raises(ValueError):
            parse_health_event(event)

