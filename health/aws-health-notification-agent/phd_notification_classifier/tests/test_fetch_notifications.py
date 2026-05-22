# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Unit tests for the fetch_phd_notifications tool."""

from unittest.mock import MagicMock, patch
import pytest
from botocore.exceptions import ClientError

from phd_notification_classifier.tools.fetch_notifications import fetch_phd_notifications

# The @tool decorator wraps the function; access the raw callable via _tool_func
_fetch = fetch_phd_notifications._tool_func

EXPECTED_FIELDS = {"arn", "service", "eventTypeCode", "eventTypeCategory", "statusCode", "region", "eventDescription", "affectedAccounts"}


def _make_event(**overrides):
    """Helper to build a Health API event dict."""
    base = {
        "arn": "arn:aws:health:us-east-1::event/EC2/OPERATIONAL_ISSUE/123",
        "service": "EC2",
        "eventTypeCode": "AWS_EC2_OPERATIONAL_ISSUE",
        "eventTypeCategory": "accountNotification",
        "statusCode": "open",
        "region": "us-east-1",
    }
    base.update(overrides)
    return base


def _make_description_response(arn, description_text="Some description"):
    """Helper to build a describe_event_details_for_organization response."""
    return {
        "successfulSet": [
            {
                "awsAccountId": "123456789012",
                "event": {"arn": arn},
                "eventDescription": {"latestDescription": description_text},
            }
        ],
        "failedSet": [],
    }



@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_pagination_collects_all_events(mock_boto3):
    """Mock API returning multiple pages, verify all events collected."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    event_page1 = _make_event(arn="arn:page1:event1", service="EC2")
    event_page2 = _make_event(arn="arn:page1:event2", service="RDS")
    event_page3 = _make_event(arn="arn:page2:event1", service="LAMBDA")

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [
        {"events": [event_page1, event_page2]},
        {"events": [event_page3]},
    ]

    mock_client.describe_event_details_for_organization.side_effect = [
        _make_description_response("arn:page1:event1", "desc1"),
        _make_description_response("arn:page1:event2", "desc2"),
        _make_description_response("arn:page2:event1", "desc3"),
    ]
    mock_client.describe_affected_accounts_for_organization.return_value = {
        "affectedAccounts": ["111111111111"]
    }

    # Call the underlying function, not the @tool wrapper
    result = _fetch()

    assert len(result) == 3
    arns = [r["arn"] for r in result]
    assert "arn:page1:event1" in arns
    assert "arn:page1:event2" in arns
    assert "arn:page2:event1" in arns

@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_status_filtering_only_open_and_upcoming(mock_boto3):
    """Verify paginator is called with filter for only open/upcoming events."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [{"events": []}]

    _fetch()

    mock_client.get_paginator.assert_called_once_with("describe_events_for_organization")
    mock_paginator.paginate.assert_called_once_with(
        filter={"eventStatusCodes": ["open", "upcoming"]}
    )



@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_empty_api_response(mock_boto3):
    """Mock API returning no events, verify empty list returned."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [{"events": []}]

    result = _fetch()

    assert result == []


@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_api_failure_propagates(mock_boto3):
    """Mock describe_events_for_organization raising ClientError, verify exception propagates."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
        "DescribeEventsForOrganization",
    )

    with pytest.raises(ClientError):
        _fetch()



@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_description_fetch_failure_returns_empty_description(mock_boto3):
    """Mock describe_event_details raising exception for one event, verify empty eventDescription."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    event1 = _make_event(arn="arn:event1", service="EC2")
    event2 = _make_event(arn="arn:event2", service="RDS")

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [{"events": [event1, event2]}]

    # First call succeeds, second raises
    mock_client.describe_event_details_for_organization.side_effect = [
        _make_description_response("arn:event1", "good description"),
        Exception("API timeout"),
    ]
    mock_client.describe_affected_accounts_for_organization.return_value = {
        "affectedAccounts": []
    }

    result = _fetch()

    assert len(result) == 2
    by_arn = {r["arn"]: r for r in result}
    assert by_arn["arn:event1"]["eventDescription"] == "good description"
    assert by_arn["arn:event2"]["eventDescription"] == ""


@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_field_extraction_returns_only_expected_fields(mock_boto3):
    """Verify only expected fields are returned in each notification dict."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    # Event with extra fields that should be stripped
    event = _make_event(
        arn="arn:event1",
        service="CASSANDRA",
        extraField="should_not_appear",
        availabilityZone="us-east-1a",
    )

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [{"events": [event]}]

    mock_client.describe_event_details_for_organization.return_value = (
        _make_description_response("arn:event1", "test desc")
    )
    mock_client.describe_affected_accounts_for_organization.return_value = {
        "affectedAccounts": ["222222222222"]
    }

    result = _fetch()

    assert len(result) == 1
    assert set(result[0].keys()) == EXPECTED_FIELDS
    assert result[0]["arn"] == "arn:event1"
    assert result[0]["service"] == "CASSANDRA"
    assert result[0]["eventDescription"] == "test desc"


@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_affected_accounts_fetched_for_each_event(mock_boto3):
    """Verify affectedAccounts included for each event via describe_affected_accounts_for_organization."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    event1 = _make_event(arn="arn:event1", service="EC2")
    event2 = _make_event(arn="arn:event2", service="RDS")

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [{"events": [event1, event2]}]

    mock_client.describe_event_details_for_organization.side_effect = [
        _make_description_response("arn:event1", "desc1"),
        _make_description_response("arn:event2", "desc2"),
    ]
    mock_client.describe_affected_accounts_for_organization.side_effect = [
        {"affectedAccounts": ["111111111111", "222222222222"]},
        {"affectedAccounts": ["333333333333"]},
    ]

    result = _fetch()

    assert len(result) == 2
    by_arn = {r["arn"]: r for r in result}
    assert by_arn["arn:event1"]["affectedAccounts"] == ["111111111111", "222222222222"]
    assert by_arn["arn:event2"]["affectedAccounts"] == ["333333333333"]

    # Verify the API was called with the correct ARNs
    calls = mock_client.describe_affected_accounts_for_organization.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["eventArn"] == "arn:event1"
    assert calls[1].kwargs["eventArn"] == "arn:event2"


@patch("phd_notification_classifier.tools.fetch_notifications.boto3")
def test_affected_accounts_fetch_failure_returns_empty_list(mock_boto3):
    """Verify affectedAccounts is empty list when describe_affected_accounts fails."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client

    event = _make_event(arn="arn:event1", service="EC2")

    mock_paginator = MagicMock()
    mock_client.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [{"events": [event]}]

    mock_client.describe_event_details_for_organization.return_value = (
        _make_description_response("arn:event1", "desc")
    )
    mock_client.describe_affected_accounts_for_organization.side_effect = Exception(
        "Access denied"
    )

    result = _fetch()

    assert len(result) == 1
    assert result[0]["affectedAccounts"] == []
