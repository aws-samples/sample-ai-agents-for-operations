# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Invoke the Amazon Bedrock AgentCore Runtime endpoint with AWS Health event payloads."""

import json
import logging
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from aha_eventbridge_lambda.response_parser import read_streaming_response

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY_SECONDS = 1

# Required IAM permissions (defined in aha_eventbridge_lambda/template.yaml):
# {
#   "Effect": "Allow",
#   "Action": "bedrock-agentcore:InvokeAgentRuntime",
#   "Resource": "${AgentRuntimeEndpointArn}*"
# }
# The Resource is scoped to the specific AgentCore Runtime ARN passed via
# AGENT_RUNTIME_ENDPOINT_ARN environment variable. The wildcard suffix allows
# session sub-resource access required by the API.
_client = boto3.client(
    "bedrock-agentcore",
    config=Config(read_timeout=300, connect_timeout=10),
)

_TRANSIENT_ERROR_CODES = {"ThrottlingException", "TooManyRequestsException"}


def _is_transient_error(exc: Exception) -> bool:
    """Return True if the exception represents a transient error eligible for retry.

    Transient errors include:
    - Throttling exceptions (ThrottlingException, TooManyRequestsException)
    - Connection timeouts
    - HTTP 5xx server errors

    Permanent errors (4xx except throttling) return False.
    """
    if isinstance(exc, ClientError):
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in _TRANSIENT_ERROR_CODES:
            return True
        http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return http_status >= 500

    # Connection timeouts and stream interruptions are transient
    if isinstance(exc, (ConnectionError, TimeoutError, RuntimeError)):
        return True

    return False


def invoke_agentcore(parsed_event: dict, endpoint_arn: str) -> str:
    """Invoke Amazon Bedrock AgentCore Runtime with the health event payload.

    Constructs a JSON payload containing the event description, status code,
    affected accounts, and event type category.  Uses the event ARN as the
    session ID so that subsequent updates to the same health event share
    conversation context.

    Reads the streaming response to completion and returns the assembled
    result string.  Retries transient errors up to 3 times with exponential
    backoff (1s, 2s, 4s).  Raises immediately on permanent errors.

    Args:
        parsed_event: Dict produced by ``parse_health_event()`` with keys
            ``event_arn``, ``status_code``, ``affected_accounts``,
            ``event_type_category``, and ``event_description``.
        endpoint_arn: The AgentCore Runtime endpoint ARN
            (from ``AGENT_RUNTIME_ENDPOINT_ARN``).

    Returns:
        The fully assembled response string from the AgentCore agent.

    Raises:
        ClientError: If a permanent error occurs or all retries are exhausted.
        RuntimeError: If the streaming response is interrupted and all retries
            are exhausted.
    """
    session_id = parsed_event["event_arn"]

    health_event = {
        "arn": parsed_event["event_arn"],
        "service": parsed_event.get("service", "unknown"),
        "eventTypeCode": parsed_event.get("event_type_code", ""),
        "eventTypeCategory": parsed_event["event_type_category"],
        "statusCode": parsed_event["status_code"],
        "eventDescription": parsed_event["event_description"],
        "affectedAccounts": parsed_event["affected_accounts"],
        "affectedEntities": parsed_event.get("affected_entities", []),
    }

    payload = json.dumps({"health_event": [health_event]}).encode()

    logger.info(
        json.dumps({
            "message": "Invoking Amazon Bedrock AgentCore Runtime",
            "event_arn": session_id,
            "session_id": session_id,
            "endpoint_arn": endpoint_arn,
        })
    )

    last_exception: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = _client.invoke_agent_runtime(
                agentRuntimeArn=endpoint_arn,
                runtimeSessionId=session_id,
                payload=payload,
            )
            return read_streaming_response(response, session_id)
        except Exception as exc:
            last_exception = exc

            if not _is_transient_error(exc):
                logger.warning(
                    json.dumps({
                        "message": "Permanent error invoking Amazon Bedrock AgentCore Runtime",
                        "event_arn": session_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    })
                )
                raise

            if attempt < MAX_RETRIES:
                delay = BASE_DELAY_SECONDS * (2 ** attempt)
                logger.warning(
                    json.dumps({
                        "message": "Transient error, retrying",
                        "event_arn": session_id,
                        "attempt": attempt + 1,
                        "max_retries": MAX_RETRIES,
                        "delay_seconds": delay,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    })
                )
                time.sleep(delay)

    # All retries exhausted
    logger.warning(
        json.dumps({
            "message": "All retries exhausted for AgentCore invocation",
            "event_arn": session_id,
            "total_attempts": MAX_RETRIES + 1,
            "error_type": type(last_exception).__name__,
            "error_message": str(last_exception),
        })
    )
    raise last_exception  # type: ignore[misc]


