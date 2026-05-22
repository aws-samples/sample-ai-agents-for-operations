# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Property-based tests for handler routing logic.

Uses Hypothesis to verify routing properties across randomly generated inputs.
The handler reads env vars at module level, so we mock os.environ and
downstream functions (parse_health_event, invoke_agentcore, publish_to_sns).
"""

import importlib
import logging
import os
from unittest.mock import patch, MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st


# --- Strategies ---

KNOWN_AGENTCORE_CATEGORIES = ["issue", "investigation"]
KNOWN_SNS_CATEGORIES = ["scheduledChange", "accountNotification"]
ALL_KNOWN_CATEGORIES = KNOWN_AGENTCORE_CATEGORIES + KNOWN_SNS_CATEGORIES

# Random category strings that are NOT in the known set
unknown_category = st.text(min_size=1, max_size=50).filter(
    lambda c: c not in ALL_KNOWN_CATEGORIES
)

# Any category: known + random unknown
any_category = st.one_of(
    st.sampled_from(ALL_KNOWN_CATEGORIES),
    unknown_category,
)

event_arn = st.from_regex(
    r"arn:aws:health:[a-z]{2}-[a-z]+-[0-9]::[a-z]+/[A-Z0-9_]+/[A-Z0-9_]+/[a-f0-9]+",
    fullmatch=True,
)

service_name = st.sampled_from(["EC2", "RDS", "LAMBDA", "S3", "ECS", "EKS", "DYNAMODB"])
status_code = st.sampled_from(["open", "closed", "upcoming"])
description_text = st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",)))
aws_account_id = st.from_regex(r"[0-9]{12}", fullmatch=True)


def make_parsed_event(arn, svc, cat, status, desc, accounts):
    """Build a parsed health event dict."""
    return {
        "event_arn": arn,
        "service": svc,
        "event_type_category": cat,
        "status_code": status,
        "event_description": desc,
        "affected_accounts": accounts,
    }


# Feature: aha-eventbridge-lambda, Property 3: Routing is determined by event type category
# **Validates: Requirements 2.1, 2.2, 2.3**
class TestProperty3RoutingByEventTypeCategory:
    """Property 3: Routing is determined by event type category.

    For any parsed health event, if the event type category is "issue" or
    "investigation" then the Lambda shall invoke AgentCore; if the event type
    category is "scheduledChange" or "accountNotification" then the Lambda
    shall publish to SNS; if the event type category is any other string then
    the Lambda shall publish to SNS.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=any_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=3),
    )
    @settings(max_examples=100)
    def test_routing_matches_category(self, arn, svc, cat, status, desc, accounts):
        """The correct downstream function is called based on event type category."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)
        raw_event = {"source": "aws.health", "detail": {"eventArn": arn}}

        env = {
            "AGENT_RUNTIME_ENDPOINT_ARN": "arn:aws:bedrock:us-east-1:123456789012:agent-runtime/test",
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:test-topic",
            "LOG_LEVEL": "WARNING",
        }

        with patch.dict(os.environ, env, clear=False):
            # Reload handler so module-level env reads pick up our mocked values
            import aha_eventbridge_lambda.handler as handler_mod
            importlib.reload(handler_mod)

            # Provide a valid JSON response for AgentCore so the handler can parse it
            agent_json = '{"classification_category":"BREAKING_CHANGE","classification_reason":"test","affected_service":"EC2","affected_accounts":[]}'

            with (
                patch.object(handler_mod, "parse_health_event", return_value=parsed) as mock_parse,
                patch.object(handler_mod, "invoke_agentcore", return_value=agent_json) as mock_agentcore,
                patch.object(handler_mod, "publish_to_sns", return_value={"MessageId": "m1"}) as mock_sns,
                patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m2"}) as mock_summary_sns,
            ):
                handler_mod.handler(raw_event, None)

                if cat in {"issue", "investigation"}:
                    mock_agentcore.assert_called_once()
                    mock_summary_sns.assert_called_once()
                    mock_sns.assert_not_called()
                else:
                    # scheduledChange, accountNotification, or unrecognized → SNS
                    mock_sns.assert_called_once()
                    mock_agentcore.assert_not_called()
                    mock_summary_sns.assert_not_called()

# Feature: aha-eventbridge-lambda, Property 9: All log entries are structured JSON with required context
# **Validates: Requirements 11.1, 11.2, 11.4, 11.5**
class TestProperty9StructuredJsonLogging:
    """Property 9: All log entries are structured JSON with required context.

    For any event processed by the Lambda, every log entry shall be valid JSON.
    INFO-level logs for event receipt shall include the event ARN and event type
    category. ERROR-level logs shall include the event ARN, error type, and
    error message.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=st.sampled_from(ALL_KNOWN_CATEGORIES),
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=3),
    )
    @settings(max_examples=100, deadline=None)
    def test_info_logs_are_structured_json_with_context(self, arn, svc, cat, status, desc, accounts):
        """INFO log entries are valid JSON containing event_arn and event_type_category."""
        import io
        import json as json_mod

        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)
        raw_event = {"source": "aws.health", "detail": {"eventArn": arn}}

        env = {
            "AGENT_RUNTIME_ENDPOINT_ARN": "arn:aws:bedrock:us-east-1:123456789012:agent-runtime/test",
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:test-topic",
            "LOG_LEVEL": "DEBUG",
        }

        with patch.dict(os.environ, env, clear=False):
            import aha_eventbridge_lambda.handler as handler_mod
            importlib.reload(handler_mod)

            # Attach a StringIO stream handler with the JSON formatter to capture output
            log_stream = io.StringIO()
            stream_handler = logging.StreamHandler(log_stream)
            stream_handler.setFormatter(handler_mod._JsonFormatter())
            handler_mod.logger.addHandler(stream_handler)
            handler_mod.logger.setLevel(logging.DEBUG)

            try:
                agent_json = '{"classification_category":"BREAKING_CHANGE","classification_reason":"test","affected_service":"EC2","affected_accounts":[]}'
                with (
                    patch.object(handler_mod, "parse_health_event", return_value=parsed),
                    patch.object(handler_mod, "invoke_agentcore", return_value=agent_json),
                    patch.object(handler_mod, "publish_to_sns", return_value={"MessageId": "m1"}),
                    patch.object(handler_mod, "publish_summary_to_sns", return_value={"MessageId": "m2"}),
                ):
                    handler_mod.handler(raw_event, None)
            finally:
                handler_mod.logger.removeHandler(stream_handler)

            log_output = log_stream.getvalue()
            assert log_output, "Expected log output but got none"

            lines = [line for line in log_output.strip().split("\n") if line.strip()]
            for line in lines:
                outer = json_mod.loads(line)  # Must be valid JSON
                assert "timestamp" in outer
                assert "level" in outer
                assert "message" in outer

                # The inner message is itself a JSON string from the handler
                inner = json_mod.loads(outer["message"])

                if outer["level"] == "INFO" and "event_arn" in inner:
                    assert inner["event_arn"] == arn
                    # Logs after parsing should include event_type_category
                    if inner.get("message") in ("Health event parsed", "Routing to AgentCore", "Routing to SNS"):
                        assert "event_type_category" in inner
                        assert inner["event_type_category"] == cat

    @given(
        arn=event_arn,
        svc=service_name,
        cat=st.sampled_from(KNOWN_AGENTCORE_CATEGORIES),
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=3),
    )
    @settings(max_examples=100, deadline=None)
    def test_error_logs_are_structured_json_with_context(self, arn, svc, cat, status, desc, accounts):
        """ERROR log entries are valid JSON containing event_arn, error_type, and error_message."""
        import io
        import json as json_mod

        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)
        raw_event = {"source": "aws.health", "detail": {"eventArn": arn}}

        env = {
            "AGENT_RUNTIME_ENDPOINT_ARN": "arn:aws:bedrock:us-east-1:123456789012:agent-runtime/test",
            "SNS_TOPIC_ARN": "arn:aws:sns:us-east-1:123456789012:test-topic",
            "LOG_LEVEL": "DEBUG",
        }

        with patch.dict(os.environ, env, clear=False):
            import aha_eventbridge_lambda.handler as handler_mod
            importlib.reload(handler_mod)

            log_stream = io.StringIO()
            stream_handler = logging.StreamHandler(log_stream)
            stream_handler.setFormatter(handler_mod._JsonFormatter())
            handler_mod.logger.addHandler(stream_handler)
            handler_mod.logger.setLevel(logging.DEBUG)

            error_msg = f"Simulated failure for {arn}"

            try:
                with (
                    patch.object(handler_mod, "parse_health_event", return_value=parsed),
                    patch.object(handler_mod, "invoke_agentcore", side_effect=RuntimeError(error_msg)),
                    patch.object(handler_mod, "publish_to_sns", return_value={"MessageId": "m1"}),
                ):
                    with pytest.raises(RuntimeError):
                        handler_mod.handler(raw_event, None)
            finally:
                handler_mod.logger.removeHandler(stream_handler)

            log_output = log_stream.getvalue()
            lines = [line for line in log_output.strip().split("\n") if line.strip()]

            error_lines = []
            for line in lines:
                outer = json_mod.loads(line)  # Must be valid JSON
                assert "timestamp" in outer
                assert "level" in outer
                assert "message" in outer

                if outer["level"] == "ERROR":
                    inner = json_mod.loads(outer["message"])
                    error_lines.append(inner)

            # At least one ERROR log should have been emitted
            assert len(error_lines) > 0, "Expected at least one ERROR log entry"

            for inner in error_lines:
                assert "event_arn" in inner, f"ERROR log missing event_arn: {inner}"
                assert inner["event_arn"] == arn
                assert "error_type" in inner, f"ERROR log missing error_type: {inner}"
                assert "error_message" in inner, f"ERROR log missing error_message: {inner}"

