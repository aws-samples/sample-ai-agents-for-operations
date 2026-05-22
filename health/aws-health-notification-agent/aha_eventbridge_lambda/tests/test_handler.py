# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Unit tests for handler.py integration.

Tests the Lambda handler entry point by mocking the downstream
functions and verifying routing, error handling, logging, and
environment variable behaviour.

Requirements: 1.1, 2.1, 2.2, 2.3, 4.3, 5.4, 6.2, 8.4, 8.5, 8.6,
              12.6, 12.7, 12.8, 12.9
"""

import importlib
import json
import logging
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENV = {
    "AGENT_RUNTIME_ENDPOINT_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
    "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:test-topic",
    "LOG_LEVEL": "INFO",
}


def _raw_event(arn="arn:aws:health:us-east-1::event/EC2/ISSUE/abc123"):
    """Minimal raw EventBridge event dict."""
    return {"source": "aws.health", "detail": {"eventArn": arn}}


def _parsed(category="issue", **overrides):
    """Build a parsed health event dict."""
    base = {
        "event_arn": "arn:aws:health:us-east-1::event/EC2/ISSUE/abc123",
        "status_code": "open",
        "affected_accounts": ["111111111111", "222222222222"],
        "event_type_category": category,
        "event_description": "Investigating connectivity issues.",
        "service": "EC2",
    }
    base.update(overrides)
    return base


def _agent_json_response(**overrides):
    """Return a valid Agent_Classification_Result JSON string."""
    base = {
        "classification_category": "BREAKING_CHANGE",
        "classification_reason": "API deprecation",
        "affected_service": "EC2",
        "affected_accounts": [
            {"account_id": "111111111111", "environment_type": "production"},
        ],
    }
    base.update(overrides)
    return json.dumps(base)


def _reload_handler(env=None):
    """Reload the handler module with the given environment variables."""
    with patch.dict(os.environ, env or _ENV, clear=False):
        import aha_eventbridge_lambda.handler as handler_mod
        importlib.reload(handler_mod)
        return handler_mod


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestHappyPathIssueEvent:
    """Req 2.1, 12.6, 12.7: issue event → AgentCore → summary → SNS."""

    def test_issue_event_publishes_summary_to_sns(self):
        handler_mod = _reload_handler()
        parsed = _parsed("issue")
        agent_resp = _agent_json_response()

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", return_value=agent_resp),
            patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m1"}) as mock_pub,
            patch.object(handler_mod, "publish_to_sns") as mock_sns,
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200
        mock_pub.assert_called_once()
        call_kwargs = mock_pub.call_args[1]
        assert "BREAKING_CHANGE" in call_kwargs["subject"]
        assert "EC2" in call_kwargs["subject"]
        mock_sns.assert_not_called()

    def test_investigation_event_publishes_summary_to_sns(self):
        handler_mod = _reload_handler()
        parsed = _parsed("investigation")
        agent_resp = _agent_json_response(
            classification_category="SECURITY_RELATED",
            affected_service="IAM",
        )

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", return_value=agent_resp),
            patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m2"}) as mock_pub,
            patch.object(handler_mod, "publish_to_sns") as mock_sns,
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200
        mock_pub.assert_called_once()
        assert "SECURITY_RELATED" in mock_pub.call_args[1]["subject"]
        mock_sns.assert_not_called()

    def test_investigation_with_impact_and_cost(self):
        """Req 12.2, 12.3, 12.4: full classification result with all sections."""
        handler_mod = _reload_handler()
        parsed = _parsed("investigation")
        agent_resp = _agent_json_response(
            impact_analysis={"summary": "High risk", "risk_level": "HIGH", "action_required": True},
            cost_projection={"details": "$500/month"},
        )

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", return_value=agent_resp),
            patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m3"}) as mock_pub,
            patch.object(handler_mod, "publish_to_sns"),
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200
        summary = mock_pub.call_args[1]["summary"]
        assert "Impact Analysis" in summary
        assert "Cost Projection" in summary


class TestHappyPathScheduledChange:
    """Req 2.2: scheduledChange event → SNS publication → success."""

    def test_scheduled_change_routes_to_sns(self):
        handler_mod = _reload_handler()
        parsed = _parsed("scheduledChange")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore") as mock_ac,
            patch.object(handler_mod, "publish_to_sns", return_value={"MessageId": "m1"}) as mock_sns,
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200
        assert "SNS" in result["body"]
        mock_sns.assert_called_once_with(parsed, handler_mod.SNS_TOPIC_ARN)
        mock_ac.assert_not_called()

    def test_account_notification_routes_to_sns(self):
        handler_mod = _reload_handler()
        parsed = _parsed("accountNotification")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore") as mock_ac,
            patch.object(handler_mod, "publish_to_sns", return_value={"MessageId": "m2"}) as mock_sns,
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200
        mock_sns.assert_called_once()
        mock_ac.assert_not_called()


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_empty_affected_accounts(self):
        handler_mod = _reload_handler()
        parsed = _parsed("issue", affected_accounts=[])
        agent_resp = _agent_json_response()

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", return_value=agent_resp),
            patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m1"}),
            patch.object(handler_mod, "publish_to_sns"),
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200

    def test_empty_event_description(self):
        handler_mod = _reload_handler()
        parsed = _parsed("scheduledChange", event_description="")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore"),
            patch.object(handler_mod, "publish_to_sns", return_value={"MessageId": "m1"}),
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200

    def test_stream_interruption_raises(self):
        """Req 4.3: Stream interruption propagates as an error."""
        handler_mod = _reload_handler()
        parsed = _parsed("issue")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", side_effect=RuntimeError("stream interrupted")),
            patch.object(handler_mod, "publish_to_sns"),
        ):
            with pytest.raises(RuntimeError, match="stream interrupted"):
                handler_mod.handler(_raw_event(), None)

    def test_sns_publish_failure_for_direct_summary_raises(self):
        """Req 5.4: SNS publish failure propagates as an error."""
        handler_mod = _reload_handler()
        parsed = _parsed("scheduledChange")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore"),
            patch.object(handler_mod, "publish_to_sns", side_effect=Exception("SNS publish failed")),
        ):
            with pytest.raises(Exception, match="SNS publish failed"):
                handler_mod.handler(_raw_event(), None)

    def test_all_retries_exhausted_raises(self):
        """Req 6.2: When AgentCore retries are exhausted, error propagates."""
        handler_mod = _reload_handler()
        parsed = _parsed("investigation")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", side_effect=RuntimeError("all retries exhausted")),
            patch.object(handler_mod, "publish_to_sns"),
        ):
            with pytest.raises(RuntimeError, match="all retries exhausted"):
                handler_mod.handler(_raw_event(), None)

    def test_agentcore_non_json_response_publishes_raw_with_warning(self):
        """Req 12.8: Non-JSON AgentCore response → raw published with warning subject."""
        handler_mod = _reload_handler()
        parsed = _parsed("issue")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", return_value="not valid json at all"),
            patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m1"}) as mock_pub,
            patch.object(handler_mod, "publish_to_sns"),
        ):
            result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200
        call_kwargs = mock_pub.call_args[1]
        assert "WARNING" in call_kwargs["subject"]
        assert parsed["event_arn"] in call_kwargs["subject"]
        assert call_kwargs["summary"] == "not valid json at all"

    def test_no_impact_no_cost_summary_has_only_required_sections(self):
        """Req 12.2: Classification result with no optional sections."""
        handler_mod = _reload_handler()
        parsed = _parsed("issue")
        agent_resp = _agent_json_response()  # no impact_analysis or cost_projection

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", return_value=agent_resp),
            patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m1"}) as mock_pub,
            patch.object(handler_mod, "publish_to_sns"),
        ):
            handler_mod.handler(_raw_event(), None)

        summary = mock_pub.call_args[1]["summary"]
        assert "Classification:" in summary
        assert "Impact Analysis" not in summary
        assert "Cost Projection" not in summary

    def test_sns_publish_failure_for_summary_raises(self):
        """Req 12.9: SNS publish failure for Human_Readable_Summary raises error."""
        handler_mod = _reload_handler()
        parsed = _parsed("issue")
        agent_resp = _agent_json_response()

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore", return_value=agent_resp),
            patch.object(handler_mod, "publish_summary_to_sns", side_effect=Exception("SNS failed")),
            patch.object(handler_mod, "publish_to_sns"),
        ):
            with pytest.raises(Exception, match="SNS failed"):
                handler_mod.handler(_raw_event(), None)


# ---------------------------------------------------------------------------
# Environment variable tests
# ---------------------------------------------------------------------------

class TestEnvironmentVariables:
    """Req 8.4, 8.5, 8.6: Environment variable handling at module level."""

    def test_missing_agent_runtime_endpoint_arn_raises_key_error(self):
        env = {
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:test-topic",
            "LOG_LEVEL": "INFO",
        }
        with patch.dict(os.environ, env, clear=True):
            import aha_eventbridge_lambda.handler as handler_mod
            with pytest.raises(KeyError, match="AGENT_RUNTIME_ENDPOINT_ARN"):
                importlib.reload(handler_mod)

    def test_missing_sns_topic_arn_raises_key_error(self):
        env = {
            "AGENT_RUNTIME_ENDPOINT_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
            "LOG_LEVEL": "INFO",
        }
        with patch.dict(os.environ, env, clear=True):
            import aha_eventbridge_lambda.handler as handler_mod
            with pytest.raises(KeyError, match="SNS_TOPIC_ARN"):
                importlib.reload(handler_mod)

    def test_missing_log_level_defaults_to_info(self):
        env = {
            "AGENT_RUNTIME_ENDPOINT_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/test",
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:test-topic",
        }
        with patch.dict(os.environ, env, clear=True):
            import aha_eventbridge_lambda.handler as handler_mod
            importlib.reload(handler_mod)
            assert handler_mod.LOG_LEVEL == "INFO"


# ---------------------------------------------------------------------------
# Unrecognized category test
# ---------------------------------------------------------------------------

class TestUnrecognizedCategory:
    """Req 2.3: Unrecognized category logs warning and publishes to SNS."""

    def test_unrecognized_category_routes_to_sns_with_warning(self, caplog):
        handler_mod = _reload_handler()
        parsed = _parsed("somethingNew")

        with (
            patch.object(handler_mod, "parse_health_event", return_value=parsed),
            patch.object(handler_mod, "invoke_agentcore") as mock_ac,
            patch.object(handler_mod, "publish_to_sns", return_value={"MessageId": "m1"}) as mock_sns,
        ):
            with caplog.at_level(logging.WARNING):
                result = handler_mod.handler(_raw_event(), None)

        assert result["statusCode"] == 200
        mock_sns.assert_called_once_with(parsed, handler_mod.SNS_TOPIC_ARN)
        mock_ac.assert_not_called()

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Unrecognized" in msg or "somethingNew" in msg for msg in warning_messages)
