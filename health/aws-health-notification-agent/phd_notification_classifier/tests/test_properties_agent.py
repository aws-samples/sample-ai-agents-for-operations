# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Property-based tests for agent classification behavior.

Feature: phd-notification-classifier
Properties 1–5: End-to-end agent classification properties

These tests invoke the real Strands Agent with Claude to verify classification
properties hold across randomly generated notification inputs. The agent
receives health event payloads directly via build_prompt — there is no
fetch_phd_notifications tool.

Each test uses template-based Hypothesis generators to produce unambiguous
notification descriptions so the LLM classification is deterministic.

NOTE: These tests require valid AWS/Bedrock credentials. They are marked with
@pytest.mark.integration and will fail without credentials.
"""

import json
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st
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
# Strategies – building blocks
# ---------------------------------------------------------------------------

_SERVICES = st.sampled_from([
    "CASSANDRA", "RDS", "EKS", "LAMBDA", "S3", "EC2", "ELASTICACHE",
    "REDSHIFT", "OPENSEARCH", "DYNAMODB",
])

_VERSIONS = st.sampled_from([
    "1.0", "1.30", "2.7", "3.6", "5.0", "8.0", "11", "14", "16", "18",
])

_DATES = st.sampled_from([
    "March 31, 2026", "June 15, 2025", "December 1, 2025",
    "January 10, 2027", "September 30, 2026",
])

_REGIONS = st.sampled_from([
    "us-east-1", "us-west-2", "eu-west-1", "ap-south-1", "global",
])

_EVENT_CATEGORIES = st.sampled_from([
    "accountNotification", "scheduledChange",
])


def _arn_strategy(service):
    """Generate a plausible event ARN for the given service."""
    return st.builds(
        lambda region, uid: (
            f"arn:aws:health:{region}::event/{service}/"
            f"AWS_{service}_PLANNED_LIFECYCLE_EVENT/{uid}"
        ),
        region=_REGIONS,
        uid=st.from_regex(r"[a-f0-9]{6}", fullmatch=True),
    )


# ---------------------------------------------------------------------------
# Notification generators per classification category
# ---------------------------------------------------------------------------

_BREAKING_TEMPLATES = [
    "{service} will be deprecated and will no longer function after {date}",
    "Applications will fail to connect to {service} after {date}",
    "{service} API endpoints will be retired on {date}. Existing workloads will stop functioning.",
    "The {service} certificate chain is changing on {date}. Without updating, your applications will fail to connect.",
]

_COST_TEMPLATES = [
    "Extended support for {service} version {version} will incur additional charges after {date}",
    "{service} version {version} is approaching end-of-standard-support on {date}. Continued use will require paid extended support.",
    "Starting {date}, {service} version {version} will enter paid extended support. Upgrade to avoid additional charges.",
]

_SECURITY_TEMPLATES = [
    "A security vulnerability has been identified in {service} requiring immediate patching",
    "A critical security patch is available for {service}. Apply the patch to address CVE-2025-0001.",
    "{service} requires a compliance update to meet new security standards effective {date}.",
    "A security advisory has been issued for {service}. Update to the latest version to remediate the vulnerability.",
]

_MIXED_TEMPLATES = [
    ("{service} version {version} will be deprecated and will no longer function after {date}. "
     "Extended support with additional charges is available until that date."),
    ("Applications will fail to connect to {service} after {date}. "
     "Paid extended support for version {version} is available to delay this change."),
]


@st.composite
def breaking_notification(draw):
    """Generate a notification with an unambiguously BREAKING_CHANGE description."""
    svc = draw(_SERVICES)
    date = draw(_DATES)
    template = draw(st.sampled_from(_BREAKING_TEMPLATES))
    description = template.format(service=svc, date=date)
    arn = draw(_arn_strategy(svc))
    return {
        "arn": arn,
        "service": svc,
        "eventTypeCode": f"AWS_{svc}_PLANNED_LIFECYCLE_EVENT",
        "eventTypeCategory": draw(_EVENT_CATEGORIES),
        "statusCode": draw(st.sampled_from(["open", "upcoming"])),
        "region": draw(_REGIONS),
        "eventDescription": description,
        "affectedAccounts": ["111111111111"],
    }


@st.composite
def cost_notification(draw):
    """Generate a notification with an unambiguously COST_IMPLICATION description."""
    svc = draw(_SERVICES)
    version = draw(_VERSIONS)
    date = draw(_DATES)
    template = draw(st.sampled_from(_COST_TEMPLATES))
    description = template.format(service=svc, version=version, date=date)
    arn = draw(_arn_strategy(svc))
    return {
        "arn": arn,
        "service": svc,
        "eventTypeCode": f"AWS_{svc}_PLANNED_LIFECYCLE_EVENT",
        "eventTypeCategory": draw(_EVENT_CATEGORIES),
        "statusCode": draw(st.sampled_from(["open", "upcoming"])),
        "region": draw(_REGIONS),
        "eventDescription": description,
        "affectedAccounts": ["111111111111"],
    }


@st.composite
def security_notification(draw):
    """Generate a notification with an unambiguously SECURITY_RELATED description."""
    svc = draw(_SERVICES)
    date = draw(_DATES)
    template = draw(st.sampled_from(_SECURITY_TEMPLATES))
    description = template.format(service=svc, date=date)
    arn = draw(_arn_strategy(svc))
    return {
        "arn": arn,
        "service": svc,
        "eventTypeCode": f"AWS_{svc}_SECURITY_NOTIFICATION",
        "eventTypeCategory": "accountNotification",
        "statusCode": draw(st.sampled_from(["open", "upcoming"])),
        "region": draw(_REGIONS),
        "eventDescription": description,
        "affectedAccounts": ["111111111111"],
    }


@st.composite
def mixed_notification(draw):
    """Generate a notification with BOTH breaking change AND cost language."""
    svc = draw(_SERVICES)
    version = draw(_VERSIONS)
    date = draw(_DATES)
    template = draw(st.sampled_from(_MIXED_TEMPLATES))
    description = template.format(service=svc, version=version, date=date)
    arn = draw(_arn_strategy(svc))
    return {
        "arn": arn,
        "service": svc,
        "eventTypeCode": f"AWS_{svc}_PLANNED_LIFECYCLE_EVENT",
        "eventTypeCategory": draw(_EVENT_CATEGORIES),
        "statusCode": draw(st.sampled_from(["open", "upcoming"])),
        "region": draw(_REGIONS),
        "eventDescription": description,
        "affectedAccounts": ["111111111111"],
    }


@st.composite
def any_notification(draw):
    """Generate a notification from any category."""
    strategy = draw(st.sampled_from([
        breaking_notification(),
        cost_notification(),
        security_notification(),
    ]))
    return draw(strategy)


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


def _invoke_agent(notification) -> str:
    """Create a Strands Agent with the 5 tools and invoke it with a payload."""
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
    payload = {"health_event": [notification]}
    prompt = build_prompt(payload)

    # Mock boto3 clients for Organizations and SNS
    mock_org = _mock_org_client()
    mock_sns = _mock_sns_client()

    def _mock_boto3_client(service_name, **kwargs):
        if service_name == "organizations":
            return mock_org
        if service_name == "sns":
            return mock_sns
        return MagicMock()

    with patch("phd_notification_classifier.tools.account_context.boto3") as mock_ac_boto3, \
         patch("phd_notification_classifier.tools.sns_notifier.boto3") as mock_sns_boto3, \
         patch.dict("os.environ", {"SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:000000000000:test"}):
        mock_ac_boto3.client.side_effect = _mock_boto3_client
        mock_sns_boto3.client.return_value = mock_sns

        result = agent(prompt)
    return str(result)


_VALID_CLASSIFICATIONS = {"BREAKING_CHANGE", "COST_IMPLICATION", "SECURITY_RELATED"}


# ---------------------------------------------------------------------------
# Property 1: Breaking changes classified BREAKING_CHANGE
# Validates: Requirements 4.1, 4.2
# ---------------------------------------------------------------------------

@pytest.mark.integration
@given(notif=breaking_notification())
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property1_breaking_changes_classified_breaking_change(notif):
    """Property 1: Breaking changes are classified BREAKING_CHANGE.

    **Validates: Requirements 4.1, 4.2**
    """
    response_text = _invoke_agent(notif)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1
    assert result["notifications"][0]["classification"] == "BREAKING_CHANGE"


# ---------------------------------------------------------------------------
# Property 2: Cost implications classified COST_IMPLICATION
# Validates: Requirements 5.1, 5.2
# ---------------------------------------------------------------------------

@pytest.mark.integration
@given(notif=cost_notification())
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property2_cost_implications_classified_cost_implication(notif):
    """Property 2: Cost implications are classified COST_IMPLICATION.

    **Validates: Requirements 5.1, 5.2**
    """
    response_text = _invoke_agent(notif)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1
    assert result["notifications"][0]["classification"] == "COST_IMPLICATION"


# ---------------------------------------------------------------------------
# Property 3: Security events classified SECURITY_RELATED
# Validates: Requirements 6.1, 6.2
# ---------------------------------------------------------------------------

@pytest.mark.integration
@given(notif=security_notification())
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property3_security_events_classified_security_related(notif):
    """Property 3: Security events are classified SECURITY_RELATED.

    **Validates: Requirements 6.1, 6.2**
    """
    response_text = _invoke_agent(notif)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1
    assert result["notifications"][0]["classification"] == "SECURITY_RELATED"


# ---------------------------------------------------------------------------
# Property 4: Classification is mutually exclusive with priority ordering
# Validates: Requirements 8.1, 8.2
# ---------------------------------------------------------------------------

@pytest.mark.integration
@given(notif=mixed_notification())
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property4_mutually_exclusive_with_priority(notif):
    """Property 4: Classification is mutually exclusive with priority ordering.

    Mixed breaking+cost language → BREAKING_CHANGE (priority rule).

    **Validates: Requirements 8.1, 8.2**
    """
    response_text = _invoke_agent(notif)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1

    classification = result["notifications"][0]["classification"]
    assert classification in _VALID_CLASSIFICATIONS
    assert classification == "BREAKING_CHANGE"


# ---------------------------------------------------------------------------
# Property 5: Every classification includes a valid reason
# Validates: Requirements 7.1, 7.2, 7.3
# ---------------------------------------------------------------------------

@pytest.mark.integration
@given(notif=any_notification())
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_property5_every_classification_has_valid_reason(notif):
    """Property 5: Every classification includes a valid reason referencing
    notification attributes.

    **Validates: Requirements 7.1, 7.2, 7.3**
    """
    response_text = _invoke_agent(notif)
    result = _extract_json(response_text)

    assert result["status"] == "success"
    assert len(result["notifications"]) >= 1

    entry = result["notifications"][0]
    reason = entry["reason"]

    # Non-empty and at least one sentence
    assert isinstance(reason, str)
    assert len(reason) > 10, f"Reason too short: '{reason}'"

    # Must reference at least one attribute of the original notification
    service = notif["service"].lower()
    event_type = notif["eventTypeCode"].lower()
    description = notif["eventDescription"].lower()
    reason_lower = reason.lower()

    desc_words = [w for w in description.split() if len(w) > 4]
    references_something = (
        service in reason_lower
        or event_type in reason_lower
        or any(word in reason_lower for word in desc_words[:10])
    )
    assert references_something, (
        f"Reason '{reason}' does not reference service '{notif['service']}', "
        f"event type '{notif['eventTypeCode']}', or description content"
    )
