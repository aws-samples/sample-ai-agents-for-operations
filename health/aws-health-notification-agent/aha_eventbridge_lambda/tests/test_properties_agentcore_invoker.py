# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Property-based tests for agentcore_invoker.invoke_agentcore().

Uses Hypothesis to verify universal properties across randomly generated inputs.
"""

import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from hypothesis import given, settings
from hypothesis import strategies as st

from aha_eventbridge_lambda.agentcore_invoker import invoke_agentcore


# --- Strategies ---

aws_account_id = st.from_regex(r"[0-9]{12}", fullmatch=True)

event_arn = st.from_regex(
    r"arn:aws:health:[a-z]{2}-[a-z]+-[0-9]::[a-z]+/[A-Z0-9_]+/[A-Z0-9_]+/[a-f0-9]+",
    fullmatch=True,
)

agentcore_category = st.sampled_from(["issue", "investigation"])

service_name = st.sampled_from(["EC2", "RDS", "LAMBDA", "S3", "ECS", "EKS", "DYNAMODB"])

status_code = st.sampled_from(["open", "closed", "upcoming"])

description_text = st.text(
    min_size=1, max_size=500, alphabet=st.characters(blacklist_categories=("Cs",))
)

endpoint_arn = st.from_regex(
    r"arn:aws:bedrock:[a-z]{2}-[a-z]+-[0-9]:[0-9]{12}:agent-runtime/[a-z0-9]+",
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


def _mock_streaming_response():
    """Return a minimal mock streaming response for invoke_agent_runtime."""
    return {
        "contentType": "application/octet-stream",
        "response": [b"ok"],
    }


# Feature: aha-eventbridge-lambda, Property 4: AgentCore payload contains all required event fields
# **Validates: Requirements 3.1, 3.3**
class TestProperty4AgentCorePayloadCompleteness:
    """Property 4: AgentCore payload contains all required event fields.

    For any health event routed to AgentCore, the JSON payload sent to the
    InvokeAgentRuntime API shall contain the event description, status code,
    affected accounts, and event type category from the original event.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=agentcore_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
        ep_arn=endpoint_arn,
    )
    @settings(max_examples=100)
    def test_payload_contains_all_required_fields(
        self, arn, svc, cat, status, desc, accounts, ep_arn
    ):
        """The payload passed to invoke_agent_runtime contains event description,
        status code, affected accounts, and event type category."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)

        mock_client = MagicMock()
        mock_client.invoke_agent_runtime.return_value = _mock_streaming_response()

        with patch("aha_eventbridge_lambda.agentcore_invoker._client", mock_client):
            invoke_agentcore(parsed, ep_arn)

        mock_client.invoke_agent_runtime.assert_called_once()
        call_kwargs = mock_client.invoke_agent_runtime.call_args[1]

        # The payload is a JSON-encoded bytes object containing a "prompt" key
        payload_bytes = call_kwargs["payload"]
        payload_obj = json.loads(payload_bytes)
        prompt = payload_obj["prompt"]

        # Verify all four required fields appear in the prompt
        assert desc in prompt, "event description missing from payload prompt"
        assert status in prompt, "status code missing from payload prompt"
        assert cat in prompt, "event type category missing from payload prompt"

        # Each affected account should appear in the prompt
        for acct in accounts:
            assert acct in prompt, f"affected account {acct} missing from payload prompt"


# Feature: aha-eventbridge-lambda, Property 5: Session ID equals event ARN
# **Validates: Requirements 3.2**
class TestProperty5SessionIdEqualsEventArn:
    """Property 5: Session ID equals event ARN.

    For any AgentCore invocation, the session ID passed to the
    InvokeAgentRuntime API shall equal the event ARN from the health event.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=agentcore_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=5),
        ep_arn=endpoint_arn,
    )
    @settings(max_examples=100)
    def test_session_id_matches_event_arn(
        self, arn, svc, cat, status, desc, accounts, ep_arn
    ):
        """The runtimeSessionId passed to invoke_agent_runtime equals the event ARN."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)

        mock_client = MagicMock()
        mock_client.invoke_agent_runtime.return_value = _mock_streaming_response()

        with patch("aha_eventbridge_lambda.agentcore_invoker._client", mock_client):
            invoke_agentcore(parsed, ep_arn)

        mock_client.invoke_agent_runtime.assert_called_once()
        call_kwargs = mock_client.invoke_agent_runtime.call_args[1]

        assert call_kwargs["runtimeSessionId"] == arn, (
            f"Expected session ID to be event ARN '{arn}', "
            f"got '{call_kwargs['runtimeSessionId']}'"
        )


# Feature: aha-eventbridge-lambda, Property 6: Streaming response is fully assembled
# **Validates: Requirements 4.1, 4.2**
class TestProperty6StreamingResponseAssembly:
    """Property 6: Streaming response is fully assembled.

    For any sequence of response chunks returned by the AgentCore Runtime,
    the invoker shall concatenate all chunks into a single result string
    whose content equals the concatenation of all individual chunk payloads.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=agentcore_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=3),
        ep_arn=endpoint_arn,
        chunks=st.lists(
            st.text(min_size=0, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
            min_size=0,
            max_size=10,
        ),
    )
    @settings(max_examples=100)
    def test_assembled_response_equals_chunk_concatenation(
        self, arn, svc, cat, status, desc, accounts, ep_arn, chunks
    ):
        """The assembled response from invoke_agentcore equals the concatenation
        of all byte chunks decoded as UTF-8."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)

        # Encode each text chunk as bytes, matching what boto3 would return
        byte_chunks = [c.encode("utf-8") for c in chunks]
        expected = "".join(chunks)

        mock_response = {
            "contentType": "application/json",
            "response": byte_chunks,
        }

        mock_client = MagicMock()
        mock_client.invoke_agent_runtime.return_value = mock_response

        with patch("aha_eventbridge_lambda.agentcore_invoker._client", mock_client):
            result = invoke_agentcore(parsed, ep_arn)

        assert result == expected, (
            f"Expected assembled response to equal concatenation of {len(chunks)} chunks.\n"
            f"Expected: {expected!r}\n"
            f"Got:      {result!r}"
        )


# Feature: aha-eventbridge-lambda, Property 8: Transient errors are retried, permanent errors are not
# **Validates: Requirements 6.1, 6.3, 6.4**
class TestProperty8RetryBehavior:
    """Property 8: Transient errors are retried, permanent errors are not.

    For any AgentCore invocation error, if the error is transient (throttling,
    timeout, or 5xx), the invoker shall retry up to 3 times with exponential
    backoff. If the error is permanent (4xx except throttling), the invoker
    shall raise immediately without retrying.
    """

    @given(
        arn=event_arn,
        svc=service_name,
        cat=agentcore_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=3),
        ep_arn=endpoint_arn,
        num_failures=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=100)
    def test_transient_errors_are_retried_then_succeed(
        self, arn, svc, cat, status, desc, accounts, ep_arn, num_failures
    ):
        """Transient errors are retried and succeed after 1-3 failures."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)

        transient_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
             "ResponseMetadata": {"HTTPStatusCode": 429}},
            "InvokeAgentRuntime",
        )

        side_effects = [transient_error] * num_failures + [_mock_streaming_response()]

        mock_client = MagicMock()
        mock_client.invoke_agent_runtime.side_effect = side_effects

        with (
            patch("aha_eventbridge_lambda.agentcore_invoker._client", mock_client),
            patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep") as mock_sleep,
        ):
            result = invoke_agentcore(parsed, ep_arn)

        # Total calls = num_failures + 1 success
        assert mock_client.invoke_agent_runtime.call_count == num_failures + 1
        # sleep called once per retry (not on the final success)
        assert mock_sleep.call_count == num_failures
        assert result == "ok"

    @given(
        arn=event_arn,
        svc=service_name,
        cat=agentcore_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=3),
        ep_arn=endpoint_arn,
        transient_error_type=st.sampled_from([
            # ThrottlingException
            lambda: ClientError(
                {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
                 "ResponseMetadata": {"HTTPStatusCode": 429}},
                "InvokeAgentRuntime",
            ),
            # TooManyRequestsException
            lambda: ClientError(
                {"Error": {"Code": "TooManyRequestsException", "Message": "Too many"},
                 "ResponseMetadata": {"HTTPStatusCode": 429}},
                "InvokeAgentRuntime",
            ),
            # HTTP 500
            lambda: ClientError(
                {"Error": {"Code": "InternalServerError", "Message": "Server error"},
                 "ResponseMetadata": {"HTTPStatusCode": 500}},
                "InvokeAgentRuntime",
            ),
            # HTTP 503
            lambda: ClientError(
                {"Error": {"Code": "ServiceUnavailableException", "Message": "Unavailable"},
                 "ResponseMetadata": {"HTTPStatusCode": 503}},
                "InvokeAgentRuntime",
            ),
            # ConnectionError
            lambda: ConnectionError("Connection refused"),
            # TimeoutError
            lambda: TimeoutError("Request timed out"),
            # RuntimeError (stream interruption)
            lambda: RuntimeError("Stream interrupted"),
        ]),
    )
    @settings(max_examples=100)
    def test_all_transient_retries_exhausted(
        self, arn, svc, cat, status, desc, accounts, ep_arn, transient_error_type
    ):
        """When all retries are exhausted for transient errors, the invoker raises
        after MAX_RETRIES + 1 total attempts."""
        from aha_eventbridge_lambda.agentcore_invoker import MAX_RETRIES

        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)
        error = transient_error_type()

        mock_client = MagicMock()
        mock_client.invoke_agent_runtime.side_effect = error

        with (
            patch("aha_eventbridge_lambda.agentcore_invoker._client", mock_client),
            patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep"),
        ):
            try:
                invoke_agentcore(parsed, ep_arn)
                assert False, "Expected exception to be raised after all retries exhausted"
            except type(error):
                pass

        # Should have attempted MAX_RETRIES + 1 times total
        assert mock_client.invoke_agent_runtime.call_count == MAX_RETRIES + 1

    @given(
        arn=event_arn,
        svc=service_name,
        cat=agentcore_category,
        status=status_code,
        desc=description_text,
        accounts=st.lists(aws_account_id, min_size=0, max_size=3),
        ep_arn=endpoint_arn,
        permanent_error=st.sampled_from([
            # ValidationException 400
            lambda: ClientError(
                {"Error": {"Code": "ValidationException", "Message": "Invalid input"},
                 "ResponseMetadata": {"HTTPStatusCode": 400}},
                "InvokeAgentRuntime",
            ),
            # AccessDeniedException 403
            lambda: ClientError(
                {"Error": {"Code": "AccessDeniedException", "Message": "Access denied"},
                 "ResponseMetadata": {"HTTPStatusCode": 403}},
                "InvokeAgentRuntime",
            ),
            # ResourceNotFoundException 404
            lambda: ClientError(
                {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"},
                 "ResponseMetadata": {"HTTPStatusCode": 404}},
                "InvokeAgentRuntime",
            ),
        ]),
    )
    @settings(max_examples=100)
    def test_permanent_errors_are_not_retried(
        self, arn, svc, cat, status, desc, accounts, ep_arn, permanent_error
    ):
        """Permanent errors (4xx except throttling) raise immediately without retry."""
        parsed = make_parsed_event(arn, svc, cat, status, desc, accounts)
        error = permanent_error()

        mock_client = MagicMock()
        mock_client.invoke_agent_runtime.side_effect = error

        with (
            patch("aha_eventbridge_lambda.agentcore_invoker._client", mock_client),
            patch("aha_eventbridge_lambda.agentcore_invoker.time.sleep") as mock_sleep,
        ):
            try:
                invoke_agentcore(parsed, ep_arn)
                assert False, "Expected ClientError to be raised for permanent error"
            except ClientError:
                pass

        # Permanent errors should NOT be retried — exactly 1 attempt
        assert mock_client.invoke_agent_runtime.call_count == 1
        # sleep should never be called for permanent errors
        assert mock_sleep.call_count == 0

