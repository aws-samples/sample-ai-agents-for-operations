"""Unit tests for trigger handlers."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mio_agent.triggers.api_handler import handler as api_handler
from mio_agent.triggers.deployment_monitor import (
    MONITORED_EVENTS,
    _extract_resource_info,
    handler as deployment_handler,
)
from mio_agent.triggers.health_event_handler import handler as health_handler
from mio_agent.triggers.support_case_handler import _parse_support_event


class TestSupportCaseHandler:
    def test_parses_direct_eventbridge_event(self):
        event = {
            "detail": {
                "case_id": "case-12345",
                "account_id": "123456789012",
                "severity": "urgent",
            }
        }
        case_id, account_id, severity = _parse_support_event(event)
        assert case_id == "case-12345"
        assert account_id == "123456789012"
        assert severity == "urgent"

    def test_parses_sns_wrapped_event(self):
        message = json.dumps({
            "case_id": "case-99999",
            "account_id": "123456789012",
            "severity": "critical",
        })
        event = {
            "Records": [{
                "EventSource": "aws:sns",
                "Sns": {"Message": message},
            }]
        }
        case_id, account_id, severity = _parse_support_event(event)
        assert case_id == "case-99999"
        assert severity == "critical"

    def test_missing_fields_return_empty_strings(self):
        event = {"detail": {}}
        case_id, account_id, severity = _parse_support_event(event)
        assert case_id == ""
        assert account_id == ""

    @patch("mio_agent.triggers.support_case_handler._enqueue_assessment")
    def test_handler_enqueues_valid_event(self, mock_enqueue):
        event = {
            "detail": {
                "case_id": "case-001",
                "account_id": "123456789012",
                "severity": "urgent",
            }
        }
        response = api_handler.__module__  # just verify import works
        mock_enqueue.return_value = None

        from mio_agent.triggers.support_case_handler import handler
        result = handler(event, None)
        assert result["statusCode"] == 200
        mock_enqueue.assert_called_once()

    @patch("mio_agent.triggers.support_case_handler._enqueue_assessment")
    def test_handler_returns_400_on_missing_account(self, mock_enqueue):
        event = {"detail": {"case_id": "case-001", "severity": "urgent"}}
        from mio_agent.triggers.support_case_handler import handler
        result = handler(event, None)
        assert result["statusCode"] == 400
        mock_enqueue.assert_not_called()


class TestDeploymentMonitor:
    def test_monitored_events_list(self):
        assert "RunInstances" in MONITORED_EVENTS
        assert "CreateFunction20150331" in MONITORED_EVENTS
        assert "CreateDBInstance" in MONITORED_EVENTS

    def test_extract_resource_info(self):
        detail = {
            "eventName": "CreateFunction20150331",
            "eventSource": "lambda.amazonaws.com",
            "awsRegion": "us-east-1",
            "requestParameters": {"functionName": "test-fn"},
        }
        info = _extract_resource_info(detail)
        assert info["event_name"] == "CreateFunction20150331"
        assert info["aws_region"] == "us-east-1"

    @patch("mio_agent.triggers.deployment_monitor._enqueue_assessment")
    def test_handler_enqueues_monitored_event(self, mock_enqueue):
        event = {
            "detail": {
                "eventName": "RunInstances",
                "recipientAccountId": "123456789012",
                "eventSource": "ec2.amazonaws.com",
                "awsRegion": "us-east-1",
            }
        }
        result = deployment_handler(event, None)
        assert result["statusCode"] == 200
        mock_enqueue.assert_called_once()

    @patch("mio_agent.triggers.deployment_monitor._enqueue_assessment")
    def test_handler_skips_unmonitored_event(self, mock_enqueue):
        event = {
            "detail": {
                "eventName": "DescribeInstances",
                "recipientAccountId": "123456789012",
            }
        }
        result = deployment_handler(event, None)
        assert result["statusCode"] == 200
        mock_enqueue.assert_not_called()


class TestHealthEventHandler:
    @patch("mio_agent.triggers.health_event_handler._enqueue_assessment")
    def test_handler_enqueues_for_each_affected_account(self, mock_enqueue):
        event = {
            "detail": {
                "eventTypeCode": "AWS_EC2_INSTANCE_ISSUE",
                "service": "EC2",
                "affectedAccounts": ["123456789012", "234567890123"],
            }
        }
        result = health_handler(event, None)
        assert result["statusCode"] == 200
        assert mock_enqueue.call_count == 2

    @patch("mio_agent.triggers.health_event_handler._enqueue_assessment")
    def test_handler_returns_200_with_no_accounts(self, mock_enqueue):
        event = {"detail": {"eventTypeCode": "AWS_EC2_ISSUE", "affectedAccounts": []}}
        result = health_handler(event, None)
        assert result["statusCode"] == 200
        mock_enqueue.assert_not_called()


class TestAPIHandler:
    @patch("mio_agent.triggers.api_handler.run_assessment")
    def test_post_assess_valid_request(self, mock_run):
        mock_oms = MagicMock()
        mock_oms.assessment_id = "test-001"
        mock_oms.overall_oms = 2.8
        mock_oms.risk_level.value = "HIGH"
        mock_oms.total_findings = 3
        mock_oms.trend = "DECLINING"
        mock_run.return_value = MagicMock(oms=mock_oms)

        event = {
            "httpMethod": "POST",
            "path": "/assess",
            "pathParameters": None,
            "body": json.dumps({
                "account_id": "123456789012",
                "account_name": "Test Account",
                "access_tier": "tier1",
                "requested_by": "test-tam",
            }),
        }
        result = api_handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["overall_oms"] == 2.8

    def test_post_assess_missing_account_id(self):
        event = {
            "httpMethod": "POST",
            "path": "/assess",
            "pathParameters": None,
            "body": json.dumps({"account_name": "Test"}),
        }
        result = api_handler(event, None)
        assert result["statusCode"] == 400

    def test_post_assess_invalid_json_body(self):
        event = {
            "httpMethod": "POST",
            "path": "/assess",
            "pathParameters": None,
            "body": "not valid json",
        }
        result = api_handler(event, None)
        assert result["statusCode"] == 400

    def test_unknown_route_returns_404(self):
        event = {
            "httpMethod": "DELETE",
            "path": "/something",
            "pathParameters": None,
            "body": None,
        }
        result = api_handler(event, None)
        assert result["statusCode"] == 404

    @patch("mio_agent.triggers.api_handler.get_accounts_list")
    def test_get_accounts(self, mock_list):
        mock_list.return_value = [
            {"account_id": "123456789012", "account_name": "Test", "access_tier": "tier3", "enabled": True}
        ]
        event = {
            "httpMethod": "GET",
            "path": "/accounts",
            "pathParameters": None,
            "body": None,
        }
        result = api_handler(event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["total"] == 1
