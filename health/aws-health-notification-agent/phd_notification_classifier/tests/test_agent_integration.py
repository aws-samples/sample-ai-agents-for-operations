# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Agent integration tests with known classification examples.

These tests invoke the real Strands Agent with Claude to verify end-to-end
classification behavior. The agent receives health event payloads directly
via build_prompt — there is no fetch_phd_notifications tool. boto3 clients
for Organizations and SNS are mocked.

NOTE: These tests require valid AWS/Bedrock credentials. They are marked with
@pytest.mark.integration and will fail without credentials.

Requirements: 1.3, 2.5, 2.6, 4.1, 5.1, 6.1, 9.1, 10.1, 11.1, 12.1, 12.4
"""

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from strands import Agent

from phd_notification_classifier.prompts import SYSTEM_PROMPT
from phd_notification_classifier.agent import build_prompt
from phd_notification_classifier.tools.account_context import get_account_context
from phd_notification_classifier.tools.consolidation import consolidate_notifications
from phd_notification_classifier.tools.impact_analyzer import analyze_impact
from phd_notification_classifier.tools.cost_estimator import estimate_cost
from phd_notification_classifier.tools.sns_notifier import publish_to_sns

_MODEL_ID = os.environ.get(
    "STRANDS_MODEL_ID",
    "eu.anthropic.claude-sonnet-4-6",
)


# ---------------------------------------------------------------------------
# Notification fixtures
# ---------------------------------------------------------------------------

KEYSPACES_NOTIFICATION = {
    "arn": "arn:aws:health:global::event/CASSANDRA/AWS_CASSANDRA_PLANNED_LIFECYCLE_EVENT/abc123",
    "service": "CASSANDRA",
    "eventTypeCode": "AWS_CASSANDRA_PLANNED_LIFECYCLE_EVENT",
    "eventTypeCategory": "scheduledChange",
    "statusCode": "upcoming",
    "region": "global",
    "eventDescription": (
        "Amazon Keyspaces will no longer include Starfield C2 in its certificate chain "
        "after March 31, 2026. This change only impacts customers who have customized "
        "their TLS-initiating applications to exclusively trust Starfield C2. Without "
        "this update, your applications may fail to connect to Amazon Keyspaces after "
        "the change."
    ),
    "affectedAccounts": ["111111111111", "222222222222"],
}

EKS_NOTIFICATION = {
    "arn": "arn:aws:health:ap-south-1::event/EKS/AWS_EKS_PLANNED_LIFECYCLE_EVENT/def456",
    "service": "EKS",
    "eventTypeCode": "AWS_EKS_PLANNED_LIFECYCLE_EVENT",
    "eventTypeCategory": "scheduledChange",
    "statusCode": "upcoming",
    "region": "ap-south-1",
    "eventDescription": (
        "Amazon EKS Kubernetes version 1.30 is entering extended support on "
        "July 23, 2026. After this date, clusters running version 1.30 will "
        "incur additional extended support charges. Your workloads will continue "
        "to run normally, but you will be billed at the extended support rate. "
        "To avoid these additional costs, we recommend upgrading your clusters "
        "to version 1.32 or higher before the extended support period begins."
    ),
    "affectedAccounts": ["333333333333"],
}

SECURITY_NOTIFICATION = {
    "arn": "arn:aws:health:us-east-1::event/RDS/AWS_RDS_SECURITY_NOTIFICATION/sec789",
    "service": "RDS",
    "eventTypeCode": "AWS_RDS_SECURITY_NOTIFICATION",
    "eventTypeCategory": "accountNotification",
    "statusCode": "open",
    "region": "us-east-1",
    "eventDescription": (
        "A security vulnerability has been identified in Amazon RDS for PostgreSQL "
        "versions 14.x and 15.x. A critical security patch is available. Apply the "
        "patch immediately to remediate CVE-2025-0042 and protect your databases."
    ),
    "affectedAccounts": ["444444444444"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Extract the first JSON object from agent response text."""
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No JSON found in agent response: {text[:200]}")


def _mock_org_client():
    """Create a mock Organizations client for account context enrichment."""
    client = MagicMock()
    client.describe_account.return_value = {
        "Account": {"Name": "test-account", "Id": "111111111111"},
    }
    client.list_parents.return_value = {
        "Parents": [{"Id": "r-root1", "Type": "ROOT"}],
    }
    client.list_tags_for_resource.return_value = {
        "Tags": [{"Key": "Environment", "Value": "production"}],
    }
    return client


def _mock_sns_client():
    """Create a mock SNS client for publish_to_sns."""
    client = MagicMock()
    client.publish.return_value = {"MessageId": "test-msg-id"}
    return client


def _invoke_agent_with_payload(payload, env_overrides=None) -> str:
    """Create a Strands Agent with the 5 tools and invoke it with a payload.

    Args:
        payload: The health event payload dict.
        env_overrides: Optional dict of environment variable overrides.
    """
    agent = Agent(
        model=_MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            get_account_context,
            consolidate_notifications,
            analyze_impact,
            estimate_cost,
            publish_to_sns,
        ],
    )
    prompt = build_prompt(payload)

    mock_org = _mock_org_client()
    mock_sns = _mock_sns_client()

    env = {"SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:000000000000:test"}
    if env_overrides:
        env.update(env_overrides)

    with patch("phd_notification_classifier.tools.account_context.boto3") as mock_ac_boto3, \
         patch("phd_notification_classifier.tools.sns_notifier.boto3") as mock_sns_boto3, \
         patch.dict("os.environ", env, clear=False):
        mock_ac_boto3.client.return_value = mock_org
        mock_sns_boto3.client.return_value = mock_sns

        result = agent(prompt)
    return str(result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_breaking_change_classification_keyspaces():
    """Keyspaces TLS certificate notification → BREAKING_CHANGE.

    Validates: Requirements 4.1, 4.2, 9.1, 11.1, 12.1
    """
    payload = {"health_event": [KEYSPACES_NOTIFICATION]}
    response_text = _invoke_agent_with_payload(payload)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1

    notification = result["notifications"][0]
    assert notification["classification"] == "BREAKING_CHANGE"
    assert notification["notification_id"] == KEYSPACES_NOTIFICATION["arn"]

    reason_lower = notification["reason"].lower()
    assert any(
        kw in reason_lower
        for kw in ["certificate", "tls", "connect", "keyspaces", "starfield"]
    ), f"Reason should reference the certificate change: {notification['reason']}"


@pytest.mark.integration
def test_cost_implication_classification_eks():
    """EKS extended support notification → COST_IMPLICATION.

    Validates: Requirements 5.1, 5.2, 10.1
    """
    payload = {"health_event": [EKS_NOTIFICATION]}
    response_text = _invoke_agent_with_payload(payload)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1

    notification = result["notifications"][0]
    assert notification["classification"] == "COST_IMPLICATION"
    assert notification["notification_id"] == EKS_NOTIFICATION["arn"]

    reason_lower = notification["reason"].lower()
    assert any(
        kw in reason_lower
        for kw in ["support", "eks", "kubernetes", "1.30", "charge", "cost"]
    ), f"Reason should reference extended support costs: {notification['reason']}"


@pytest.mark.integration
def test_security_related_classification_rds():
    """RDS security vulnerability notification → SECURITY_RELATED.

    Validates: Requirements 6.1, 6.2
    """
    payload = {"health_event": [SECURITY_NOTIFICATION]}
    response_text = _invoke_agent_with_payload(payload)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1

    notification = result["notifications"][0]
    assert notification["classification"] == "SECURITY_RELATED"
    assert notification["notification_id"] == SECURITY_NOTIFICATION["arn"]

    reason_lower = notification["reason"].lower()
    assert any(
        kw in reason_lower
        for kw in ["security", "vulnerability", "patch", "cve", "rds"]
    ), f"Reason should reference security concern: {notification['reason']}"


@pytest.mark.integration
def test_empty_payload():
    """Empty health event payload → error or empty response schema.

    Validates: Requirements 2.5
    """
    payload = {"health_event": []}
    response_text = _invoke_agent_with_payload(payload)
    result = _extract_json(response_text)

    # Agent should return success with empty notifications or error
    if result.get("status") == "success":
        assert result["notifications"] == []
        assert result["total_count"] == 0
        assert result["breaking_change_count"] == 0
        assert result["cost_implication_count"] == 0
        assert result["security_related_count"] == 0
    else:
        assert result["status"] == "error"


@pytest.mark.integration
def test_malformed_payload():
    """Malformed payload → error response.

    Validates: Requirements 1.3, 2.5
    """
    # Pass a string that is not valid JSON as the health_event
    payload = {"health_event": "this is not valid event data"}
    response_text = _invoke_agent_with_payload(payload)
    result = _extract_json(response_text)

    # The agent should handle this gracefully — either error or empty success
    assert result["status"] in ("success", "error")


@pytest.mark.integration
def test_limit_parameter():
    """Limit parameter caps the number of notifications processed.

    Validates: Requirements 2.6
    """
    payload = {
        "health_event": [KEYSPACES_NOTIFICATION, EKS_NOTIFICATION, SECURITY_NOTIFICATION],
        "limit": 1,
    }
    response_text = _invoke_agent_with_payload(payload)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) <= 1
    assert result["total_count"] <= 1


@pytest.mark.integration
def test_sns_topic_arn_not_set():
    """SNS_TOPIC_ARN not set → agent completes classification, sns_publish_status skipped.

    Validates: Requirements 12.4
    """
    payload = {"health_event": [KEYSPACES_NOTIFICATION]}

    # Override SNS_TOPIC_ARN to empty string to simulate not set
    agent = Agent(
        model=_MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            get_account_context,
            consolidate_notifications,
            analyze_impact,
            estimate_cost,
            publish_to_sns,
        ],
    )
    prompt = build_prompt(payload)

    mock_org = _mock_org_client()

    # Ensure SNS_TOPIC_ARN is NOT set
    env_copy = os.environ.copy()
    env_copy.pop("SNS_TOPIC_ARN", None)

    with patch("phd_notification_classifier.tools.account_context.boto3") as mock_ac_boto3, \
         patch.dict("os.environ", env_copy, clear=True):
        mock_ac_boto3.client.return_value = mock_org

        result_text = str(agent(prompt))

    result = _extract_json(result_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1
    # SNS publish should be skipped
    assert result.get("sns_publish_status") == "skipped"
