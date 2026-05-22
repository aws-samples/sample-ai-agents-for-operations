# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Unit tests for event_parser.parse_health_event()."""

import pytest

from aha_eventbridge_lambda.event_parser import parse_health_event


def _make_event(detail_overrides=None):
    """Build a minimal valid EventBridge health event."""
    detail = {
        "eventArn": "arn:aws:health:us-east-1::event/EC2/AWS_EC2_OPERATIONAL_ISSUE/123",
        "service": "EC2",
        "eventTypeCategory": "issue",
        "statusCode": "open",
        "eventDescription": [
            {"language": "en_US", "latestDescription": "Investigating connectivity issues."}
        ],
        "affectedEntities": [
            {"entityValue": "i-abc", "awsAccountId": "111111111111"},
            {"entityValue": "i-def", "awsAccountId": "222222222222"},
        ],
    }
    if detail_overrides:
        detail.update(detail_overrides)
    return {"detail": detail}


class TestParseHealthEvent:
    def test_extracts_all_fields(self):
        result = parse_health_event(_make_event())
        assert result["event_arn"] == "arn:aws:health:us-east-1::event/EC2/AWS_EC2_OPERATIONAL_ISSUE/123"
        assert result["status_code"] == "open"
        assert result["event_type_category"] == "issue"
        assert result["event_description"] == "Investigating connectivity issues."
        assert result["service"] == "EC2"
        assert set(result["affected_accounts"]) == {"111111111111", "222222222222"}

    def test_deduplicates_affected_accounts(self):
        event = _make_event({
            "affectedEntities": [
                {"entityValue": "i-abc", "awsAccountId": "111111111111"},
                {"entityValue": "i-def", "awsAccountId": "111111111111"},
            ]
        })
        result = parse_health_event(event)
        assert result["affected_accounts"] == ["111111111111"]

    def test_empty_affected_entities(self):
        event = _make_event({"affectedEntities": []})
        result = parse_health_event(event)
        assert result["affected_accounts"] == []

    def test_missing_affected_entities_key(self):
        detail = {
            "eventArn": "arn:aws:health:us-east-1::event/EC2/TEST/1",
            "eventTypeCategory": "issue",
        }
        result = parse_health_event({"detail": detail})
        assert result["affected_accounts"] == []

    def test_empty_event_description(self):
        event = _make_event({"eventDescription": []})
        result = parse_health_event(event)
        assert result["event_description"] == ""

    def test_missing_event_arn_raises(self):
        detail = {"eventTypeCategory": "issue", "statusCode": "open"}
        with pytest.raises(ValueError, match="eventArn"):
            parse_health_event({"detail": detail})

    def test_missing_event_type_category_raises(self):
        detail = {"eventArn": "arn:aws:health:us-east-1::event/EC2/TEST/1"}
        with pytest.raises(ValueError, match="eventTypeCategory"):
            parse_health_event({"detail": detail})

    def test_both_required_fields_missing_raises(self):
        with pytest.raises(ValueError):
            parse_health_event({"detail": {}})

    def test_defaults_for_optional_fields(self):
        detail = {
            "eventArn": "arn:aws:health:us-east-1::event/EC2/TEST/1",
            "eventTypeCategory": "scheduledChange",
        }
        result = parse_health_event({"detail": detail})
        assert result["status_code"] == ""
        assert result["service"] == ""
        assert result["event_description"] == ""
        assert result["affected_accounts"] == []
