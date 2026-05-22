# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Unit tests for agentcore_invoker.invoke_agentcore()."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from aha_eventbridge_lambda.agentcore_invoker import (
    _is_transient_error,
    invoke_agentcore,
)


def _parsed_event(**overrides):
    """Build a minimal parsed health event dict."""
    base = {
        "event_arn": "arn:aws:health:us-east-1::event/EC2/AWS_EC2_OPERATIONAL_ISSUE/123",
        "status_code": "open",
        "affected_accounts": ["111111111111", "222222222222"],
        "event_type_category": "issue",
        "event_description": "Investigating connectivity issues.",
        "service": "EC2",
    }
    base.update(overrides)
    return base


ENDPOINT_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-agent"


class TestInvokeAgentcore:
    """Tests for invoke_agentcore() covering payload, session ID, and streaming."""

    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_payload_contains_all_required_fields(self, mock_client):
        """Req 3.1, 3.3: Payload includes description, status, accounts, category."""
        mock_response = {
            "contentType": "application/json",
            "response": [b'{"result": "ok"}'],
        }
        mock_client.invoke_agent_runtime.return_value = mock_response

        invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        call_kwargs = mock_client.invoke_agent_runtime.call_args[1]
        payload_str = call_kwargs["payload"].decode("utf-8")
        payload = json.loads(payload_str)
        prompt = payload["prompt"]

        assert "Investigating connectivity issues." in prompt
        assert "open" in prompt
        assert "111111111111" in prompt
        assert "222222222222" in prompt
        assert "issue" in prompt

    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_session_id_equals_event_arn(self, mock_client):
        """Req 3.2: Session ID is the event ARN."""
        mock_response = {
            "contentType": "application/json",
            "response": [b"done"],
        }
        mock_client.invoke_agent_runtime.return_value = mock_response
        event = _parsed_event()

        invoke_agentcore(event, ENDPOINT_ARN)

        call_kwargs = mock_client.invoke_agent_runtime.call_args[1]
        assert call_kwargs["runtimeSessionId"] == event["event_arn"]


    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_endpoint_arn_passed_to_api(self, mock_client):
        """Req 3.1: Endpoint ARN is forwarded to the API call."""
        mock_response = {
            "contentType": "application/json",
            "response": [b"ok"],
        }
        mock_client.invoke_agent_runtime.return_value = mock_response

        invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        call_kwargs = mock_client.invoke_agent_runtime.call_args[1]
        assert call_kwargs["agentRuntimeArn"] == ENDPOINT_ARN

    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_json_response_assembled(self, mock_client):
        """Req 4.1, 4.2: Multiple JSON chunks are concatenated."""
        mock_response = {
            "contentType": "application/json",
            "response": [b"hello ", b"world"],
        }
        mock_client.invoke_agent_runtime.return_value = mock_response

        result = invoke_agentcore(_parsed_event(), ENDPOINT_ARN)
        assert result == "hello world"

    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_single_chunk_response(self, mock_client):
        """Req 4.1: Single-chunk response is returned as-is."""
        mock_response = {
            "contentType": "application/json",
            "response": [b"single chunk"],
        }
        mock_client.invoke_agent_runtime.return_value = mock_response

        result = invoke_agentcore(_parsed_event(), ENDPOINT_ARN)
        assert result == "single chunk"


    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_event_stream_response_assembled(self, mock_client):
        """Req 4.1, 4.2: SSE event-stream lines are assembled."""
        lines = [b"data: line one", b"data: line two"]
        mock_stream = MagicMock()
        mock_stream.iter_lines.return_value = iter(lines)
        mock_response = {
            "contentType": "text/event-stream",
            "response": mock_stream,
        }
        mock_client.invoke_agent_runtime.return_value = mock_response

        result = invoke_agentcore(_parsed_event(), ENDPOINT_ARN)
        assert result == "line one\nline two"

    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_stream_interruption_raises_runtime_error(self, mock_client):
        """Req 4.3: Interrupted stream logs partial response and raises."""
        def _exploding_iter(chunk_size=10):
            yield b"data: partial"
            raise ConnectionError("stream reset")

        mock_stream = MagicMock()
        mock_stream.iter_lines.side_effect = _exploding_iter
        mock_response = {
            "contentType": "text/event-stream",
            "response": mock_stream,
        }
        mock_client.invoke_agent_runtime.return_value = mock_response

        with pytest.raises(RuntimeError, match="interrupted"):
            invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_empty_affected_accounts(self, mock_client):
        """Edge case: empty affected accounts list."""
        mock_response = {
            "contentType": "application/json",
            "response": [b"ok"],
        }
        mock_client.invoke_agent_runtime.return_value = mock_response
        event = _parsed_event(affected_accounts=[])

        invoke_agentcore(event, ENDPOINT_ARN)

        call_kwargs = mock_client.invoke_agent_runtime.call_args[1]
        payload = json.loads(call_kwargs["payload"].decode("utf-8"))
        assert "Affected Accounts: \n" in payload["prompt"]

    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_empty_event_description(self, mock_client):
        """Edge case: empty event description."""
        mock_response = {
            "contentType": "application/json",
            "response": [b"ok"],
        }
        mock_client.invoke_agent_runtime.return_value = mock_response
        event = _parsed_event(event_description="")

        invoke_agentcore(event, ENDPOINT_ARN)

        call_kwargs = mock_client.invoke_agent_runtime.call_args[1]
        payload = json.loads(call_kwargs["payload"].decode("utf-8"))
        assert "Description: \n" not in payload["prompt"]
        assert payload["prompt"].endswith("Description: ")


def _client_error(code: str, status: int, message: str = "error") -> ClientError:
    """Build a botocore ClientError with the given code and HTTP status."""
    return ClientError(
        {"Error": {"Code": code, "Message": message},
         "ResponseMetadata": {"HTTPStatusCode": status}},
        "InvokeAgentRuntime",
    )


class TestIsTransientError:
    """Tests for _is_transient_error() error classification."""

    def test_throttling_exception_is_transient(self):
        """Req 6.1: ThrottlingException is transient."""
        exc = _client_error("ThrottlingException", 429)
        assert _is_transient_error(exc) is True

    def test_too_many_requests_is_transient(self):
        """Req 6.1: TooManyRequestsException is transient."""
        exc = _client_error("TooManyRequestsException", 429)
        assert _is_transient_error(exc) is True

    def test_5xx_is_transient(self):
        """Req 6.1: HTTP 500 server error is transient."""
        exc = _client_error("InternalServerError", 500)
        assert _is_transient_error(exc) is True

    def test_503_is_transient(self):
        """Req 6.1: HTTP 503 service unavailable is transient."""
        exc = _client_error("ServiceUnavailable", 503)
        assert _is_transient_error(exc) is True

    def test_4xx_non_throttling_is_permanent(self):
        """Req 6.3: HTTP 400 (non-throttling) is permanent."""
        exc = _client_error("ValidationException", 400)
        assert _is_transient_error(exc) is False

    def test_403_is_permanent(self):
        """Req 6.3: HTTP 403 access denied is permanent."""
        exc = _client_error("AccessDeniedException", 403)
        assert _is_transient_error(exc) is False

    def test_404_is_permanent(self):
        """Req 6.3: HTTP 404 not found is permanent."""
        exc = _client_error("ResourceNotFoundException", 404)
        assert _is_transient_error(exc) is False

    def test_connection_error_is_transient(self):
        """Req 6.1: Connection errors are transient."""
        assert _is_transient_error(ConnectionError("reset")) is True

    def test_timeout_error_is_transient(self):
        """Req 6.1: Timeout errors are transient."""
        assert _is_transient_error(TimeoutError("timed out")) is True

    def test_runtime_error_is_transient(self):
        """Req 6.1: RuntimeError (stream interruption) is transient."""
        assert _is_transient_error(RuntimeError("stream interrupted")) is True

    def test_unknown_exception_is_not_transient(self):
        """Unknown exception types are treated as permanent."""
        assert _is_transient_error(ValueError("bad value")) is False


class TestRetryLogic:
    """Tests for retry behavior in invoke_agentcore()."""

    @patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep")
    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_transient_error_retried_then_succeeds(self, mock_client, mock_sleep):
        """Req 6.1: Transient error is retried and succeeds on second attempt."""
        throttle_exc = _client_error("ThrottlingException", 429)
        mock_client.invoke_agent_runtime.side_effect = [
            throttle_exc,
            {"contentType": "application/json", "response": [b"ok"]},
        ]

        result = invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        assert result == "ok"
        assert mock_client.invoke_agent_runtime.call_count == 2
        mock_sleep.assert_called_once_with(1)  # 1s delay for first retry

    @patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep")
    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_exponential_backoff_delays(self, mock_client, mock_sleep):
        """Req 6.1: Backoff delays are 1s, 2s, 4s."""
        server_err = _client_error("InternalServerError", 500)
        mock_client.invoke_agent_runtime.side_effect = [
            server_err,
            server_err,
            server_err,
            {"contentType": "application/json", "response": [b"ok"]},
        ]

        result = invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        assert result == "ok"
        assert mock_client.invoke_agent_runtime.call_count == 4
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1, 2, 4]

    @patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep")
    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_all_retries_exhausted_raises(self, mock_client, mock_sleep):
        """Req 6.2: All retries exhausted logs and raises."""
        server_err = _client_error("InternalServerError", 500)
        mock_client.invoke_agent_runtime.side_effect = server_err

        with pytest.raises(ClientError):
            invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        # 1 initial + 3 retries = 4 total attempts
        assert mock_client.invoke_agent_runtime.call_count == 4
        assert mock_sleep.call_count == 3

    @patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep")
    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_permanent_error_raises_immediately(self, mock_client, mock_sleep):
        """Req 6.4: Permanent error raises immediately without retrying."""
        perm_err = _client_error("ValidationException", 400)
        mock_client.invoke_agent_runtime.side_effect = perm_err

        with pytest.raises(ClientError):
            invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        assert mock_client.invoke_agent_runtime.call_count == 1
        mock_sleep.assert_not_called()

    @patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep")
    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_access_denied_not_retried(self, mock_client, mock_sleep):
        """Req 6.4: AccessDeniedException (403) is not retried."""
        perm_err = _client_error("AccessDeniedException", 403)
        mock_client.invoke_agent_runtime.side_effect = perm_err

        with pytest.raises(ClientError):
            invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        assert mock_client.invoke_agent_runtime.call_count == 1
        mock_sleep.assert_not_called()

    @patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep")
    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_stream_interruption_retried(self, mock_client, mock_sleep):
        """Req 6.1: Stream interruption (RuntimeError) triggers retry."""
        def _exploding_response():
            def _exploding_iter(chunk_size=10):
                yield b"data: partial"
                raise ConnectionError("stream reset")
            mock_stream = MagicMock()
            mock_stream.iter_lines.side_effect = _exploding_iter
            return {"contentType": "text/event-stream", "response": mock_stream}

        mock_client.invoke_agent_runtime.side_effect = [
            _exploding_response(),
            {"contentType": "application/json", "response": [b"success"]},
        ]

        result = invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        assert result == "success"
        assert mock_client.invoke_agent_runtime.call_count == 2
        mock_sleep.assert_called_once_with(1)

    @patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep")
    @patch("aha_eventbridge_lambda.agentcore_invoker._client")
    def test_retries_exhausted_logs_final_failure(self, mock_client, mock_sleep, caplog):
        """Req 6.2: Final failure is logged when all retries exhausted."""
        server_err = _client_error("InternalServerError", 500, "server down")
        mock_client.invoke_agent_runtime.side_effect = server_err

        with pytest.raises(ClientError):
            invoke_agentcore(_parsed_event(), ENDPOINT_ARN)

        # Check that the final exhaustion message was logged
        log_messages = [r.message for r in caplog.records]
        exhaustion_logs = [
            m for m in log_messages if "All retries exhausted" in m
        ]
        assert len(exhaustion_logs) == 1
        log_data = json.loads(exhaustion_logs[0])
        assert log_data["total_attempts"] == 4
        assert log_data["error_type"] == "ClientError"
