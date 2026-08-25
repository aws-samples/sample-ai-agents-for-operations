"""Amazon Bedrock client utilities for MIO Agent."""

from __future__ import annotations

import json
import time
import random
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

_BEDROCK_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "adaptive"},
    connect_timeout=10,
    read_timeout=120,
)

import os

# Default model — configurable via BEDROCK_MODEL_ID environment variable
DEFAULT_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-sonnet-4-20250514-v1:0",
)

# Bedrock Agents runtime endpoint
_AGENTS_RUNTIME_SERVICE = "bedrock-agent-runtime"
_BEDROCK_RUNTIME_SERVICE = "bedrock-runtime"


class BedrockClientError(Exception):
    """Raised when Bedrock API calls fail."""


class BedrockThrottlingError(BedrockClientError):
    """Raised when Bedrock throttles requests."""


def invoke_model(
    prompt: str,
    model_id: str = DEFAULT_MODEL_ID,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    region: str = "us-east-1",
    system_prompt: str | None = None,
    max_retries: int = 3,
) -> str:
    """Invoke a Bedrock model and return the text response.

    Implements exponential backoff with jitter on ThrottlingException.

    Args:
        prompt: User message to send to the model.
        model_id: Bedrock model ID to invoke.
        max_tokens: Maximum tokens in the response.
        temperature: Sampling temperature (lower = more deterministic).
        region: AWS region for Bedrock.
        system_prompt: Optional system-level instruction.
        max_retries: Maximum retry attempts on throttling (default 3).

    Returns:
        Model response text.

    Raises:
        BedrockClientError: If the invocation fails after all retries.
        BedrockThrottlingError: If throttled after max retries.
    """
    client = boto3.client(_BEDROCK_RUNTIME_SERVICE, region_name=region, config=_BEDROCK_CONFIG)

    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system_prompt:
        body["system"] = system_prompt

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = client.invoke_model(
                modelId=model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"]

        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ThrottlingException":
                last_error = e
                if attempt < max_retries - 1:
                    # Exponential backoff with jitter: 2^attempt * (0.5 to 1.5s)
                    sleep_time = (2 ** attempt) * (0.5 + random.random())  # nosec B311 - used for jitter, not cryptography
                    logger.warning(
                        "Bedrock throttled, retrying with backoff",
                        extra={"attempt": attempt + 1, "sleep_seconds": round(sleep_time, 2)},
                    )
                    time.sleep(sleep_time)
                    continue
                raise BedrockThrottlingError(
                    f"Bedrock throttled after {max_retries} attempts: {e}"
                ) from e
            raise BedrockClientError(f"Bedrock invocation failed: {e}") from e

    raise BedrockThrottlingError(f"Bedrock throttled after {max_retries} attempts") from last_error


def invoke_agent(
    agent_id: str,
    agent_alias_id: str,
    session_id: str,
    input_text: str,
    region: str = "us-east-1",
    session_attributes: dict[str, str] | None = None,
) -> str:
    """Invoke a Bedrock Agent and return the completion text.

    Args:
        agent_id: Bedrock Agent ID.
        agent_alias_id: Agent alias ID (e.g., TSTALIASID for testing).
        session_id: Unique session identifier for conversation continuity.
        input_text: Input message to the agent.
        region: AWS region.
        session_attributes: Optional session attributes to pass to the agent.

    Returns:
        Agent response text (concatenated from all chunks).

    Raises:
        BedrockClientError: If the agent invocation fails.
    """
    client = boto3.client(_AGENTS_RUNTIME_SERVICE, region_name=region, config=_BEDROCK_CONFIG)

    kwargs: dict[str, Any] = {
        "agentId": agent_id,
        "agentAliasId": agent_alias_id,
        "sessionId": session_id,
        "inputText": input_text,
    }
    if session_attributes:
        kwargs["sessionState"] = {"sessionAttributes": session_attributes}

    try:
        response = client.invoke_agent(**kwargs)
        # Stream the response chunks
        completion = ""
        for event in response["completion"]:
            if "chunk" in event:
                chunk = event["chunk"]
                completion += chunk["bytes"].decode("utf-8")
        return completion

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ThrottlingException":
            raise BedrockThrottlingError(f"Bedrock Agent throttled: {e}") from e
        raise BedrockClientError(f"Bedrock Agent invocation failed: {e}") from e


def build_assessment_prompt(
    findings_json: str,
    audience: str,
    account_name: str,
    oms_score: float,
    risk_level: str,
) -> str:
    """Build a structured prompt for narrative generation.

    Args:
        findings_json: JSON string of assessment findings.
        audience: Target audience (tam / customer / leadership).
        account_name: Customer account name.
        oms_score: Overall OMS score.
        risk_level: Risk level classification.

    Returns:
        Formatted prompt string for the Narrative Agent.
    """
    audience_instructions = {
        "tam": (
            "You are briefing a Technical Account Manager (TAM). Be concise, specific, "
            "and actionable. Focus on the top gaps, provide exact talking points, "
            "and recommend specific AWS services. Use business-friendly language."
        ),
        "customer": (
            "You are writing a customer-facing Observability Health Report. "
            "Use clear, professional language. Start with an executive summary, "
            "then provide technical details for engineers. Avoid jargon. "
            "Frame gaps as opportunities, not failures."
        ),
        "leadership": (
            "You are writing a leadership portfolio summary. Focus on business risk, "
            "trends, and high-level patterns. Use data to tell a story. "
            "Highlight the correlation between observability gaps and support costs."
        ),
    }

    instruction = audience_instructions.get(audience, audience_instructions["tam"])

    return f"""You are the MIO Agent Narrative Engine, generating observability assessment reports.

{instruction}

Account: {account_name}
Observability Maturity Score (OMS): {oms_score}/5.0
Risk Level: {risk_level}

Assessment Findings:
{findings_json}

Generate a clear, well-structured report appropriate for the {audience} audience.
Do NOT include raw JSON in the output. Use plain English with specific evidence from the findings.
"""
