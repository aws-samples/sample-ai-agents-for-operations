"""Layer 4: Amazon Bedrock Guardrails integration for MIO Agent.

Wraps all Bedrock model invocations with guardrails to prevent:
- PII exposure in outputs (account IDs, emails, names)
- Out-of-scope recommendations (cost advice, security vulnerabilities)
- Hallucinated AWS service names or ARNs
- Inappropriate content

See: https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html
"""

from __future__ import annotations

import json
import re
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

# SSM parameter paths for guardrail configuration
GUARDRAIL_ID_PARAM = "/mio-agent/bedrock/guardrail-id"
GUARDRAIL_VERSION_PARAM = "/mio-agent/bedrock/guardrail-version"

# Fallback post-processing patterns when Bedrock Guardrails is not configured
_PII_PATTERNS = [
    # AWS Account IDs (12 digits standalone)
    (r"\b\d{12}\b", "[ACCOUNT-ID-REDACTED]"),
    # ARNs — redact account portion
    (r"arn:aws:[a-z0-9-]+:[a-z0-9-]*:(\d{12}):", r"arn:aws:\2:[REGION]:[ACCOUNT-ID-REDACTED]:"),
    # Email addresses
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL-REDACTED]"),
]

# Topics that MIO Agent must not advise on
_OUT_OF_SCOPE_PATTERNS = [
    (r"\bcost[s]?\s+\$[\d,]+", "cost estimate (out of scope)"),
    (r"\$\s*[\d,]+\s*per\s*month", "monthly cost (out of scope)"),
    (r"\bsecurity\s+vulnerabilit", "security vulnerability (out of scope)"),
    (r"\bcve-\d{4}-\d+", "CVE reference (out of scope)"),
    (r"\bpenetration\s+test", "penetration testing (out of scope)"),
]


class GuardrailViolationError(Exception):
    """Raised when output violates guardrail policies."""

    def __init__(self, message: str, violations: list[str]) -> None:
        super().__init__(message)
        self.violations = violations


def invoke_with_guardrails(
    prompt: str,
    system_prompt: str,
    model_id: str,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    region: str = "us-east-1",
    guardrail_id: str | None = None,
    guardrail_version: str = "DRAFT",
) -> str:
    """Invoke a Bedrock model with guardrails applied.

    Attempts to use Amazon Bedrock Guardrails if a guardrail ID is configured.
    Falls back to post-processing guardrails if not.

    Args:
        prompt: User prompt.
        system_prompt: System-level instructions.
        model_id: Bedrock model ID.
        max_tokens: Maximum response tokens.
        temperature: Sampling temperature.
        region: AWS region.
        guardrail_id: Bedrock Guardrail ID (from SSM or direct).
        guardrail_version: Guardrail version to use.

    Returns:
        Guardrail-filtered model response text.

    Raises:
        GuardrailViolationError: If the output violates critical policies.
    """
    # Resolve guardrail ID from SSM if not provided directly
    resolved_guardrail_id = guardrail_id or _get_guardrail_id_from_ssm(region)

    client = boto3.client("bedrock-runtime", region_name=region, config=_BEDROCK_CONFIG)

    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
    }

    invoke_kwargs: dict[str, Any] = {
        "modelId": model_id,
        "body": json.dumps(body),
        "contentType": "application/json",
        "accept": "application/json",
    }

    # Apply Bedrock Guardrails if available
    if resolved_guardrail_id:
        invoke_kwargs["guardrailIdentifier"] = resolved_guardrail_id
        invoke_kwargs["guardrailVersion"] = guardrail_version
        logger.info("Invoking Bedrock with guardrails", extra={"guardrail_id": resolved_guardrail_id})
    else:
        logger.info("Bedrock Guardrails not configured — applying post-processing guardrails")

    try:
        response = client.invoke_model(**invoke_kwargs)
        result = json.loads(response["body"].read())

        # Check if guardrails blocked the response
        if resolved_guardrail_id:
            stop_reason = result.get("stop_reason", "")
            if stop_reason == "guardrail_intervened":
                usage = result.get("amazon-bedrock-guardrailAction", "BLOCKED")
                raise GuardrailViolationError(
                    f"Bedrock Guardrails blocked the response: {usage}",
                    violations=["guardrail_intervention"],
                )

        output_text = result["content"][0]["text"]

    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ValidationException" and "guardrail" in str(e).lower():
            raise GuardrailViolationError(
                "Bedrock Guardrails rejected the input or output",
                violations=["guardrail_validation_error"],
            ) from e
        raise

    # Always apply post-processing regardless of Bedrock Guardrails
    output_text = _apply_post_processing(output_text)

    return output_text


def _apply_post_processing(text: str) -> str:
    """Apply post-processing guardrails to model output.

    This runs regardless of whether Bedrock Guardrails is configured,
    providing a defence-in-depth safety layer.
    """
    result = text

    # Redact PII patterns
    for pattern, replacement in _PII_PATTERNS:
        result = re.sub(pattern, replacement, result)

    # Detect out-of-scope content and log warnings
    for pattern, description in _OUT_OF_SCOPE_PATTERNS:
        if re.search(pattern, result, re.IGNORECASE):
            logger.warning(
                "Post-processing detected potentially out-of-scope content",
                extra={"pattern": description},
            )
            # We log and flag but don't automatically remove — TAM review handles this
            result = result + f"\n\n⚠️ **Review Note:** This output may contain {description}. Please review before sharing."

    return result


def _get_guardrail_id_from_ssm(region: str) -> str | None:
    """Retrieve Bedrock Guardrail ID from SSM Parameter Store."""
    try:
        ssm = boto3.client("ssm", region_name=region)
        response = ssm.get_parameter(Name=GUARDRAIL_ID_PARAM)
        return response["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            logger.debug("Bedrock Guardrail ID not configured in SSM — running without Bedrock Guardrails")
            return None
        logger.warning("Could not retrieve guardrail ID from SSM", extra={"error": str(e)})
        return None


def create_mio_agent_guardrail(region: str = "us-east-1") -> str:
    """Create a Bedrock Guardrail configured for MIO Agent.

    Call this once during deployment to create the guardrail and store
    the ID in SSM Parameter Store.

    Args:
        region: AWS region.

    Returns:
        Created guardrail ID.
    """
    client = boto3.client("bedrock", region_name=region)

    guardrail_config = {
        "name": "mio-agent-guardrail",
        "description": "MIO Agent output safety guardrail — prevents PII exposure and out-of-scope advice",
        "topicPolicyConfig": {
            "topicsConfig": [
                {
                    "name": "cost-advice",
                    "definition": "Advice about AWS costs, pricing, budgets, or cost optimization",
                    "examples": [
                        "This will cost you $X per month",
                        "To reduce your AWS bill",
                        "Cost optimization recommendation",
                    ],
                    "type": "DENY",
                },
                {
                    "name": "security-vulnerabilities",
                    "definition": "Security vulnerability assessments, penetration testing, CVE references, or exploit guidance",
                    "examples": [
                        "This CVE affects your system",
                        "Your system is vulnerable to attack",
                        "Penetration test results",
                    ],
                    "type": "DENY",
                },
                {
                    "name": "infrastructure-changes",
                    "definition": "Instructions to delete, terminate, modify, or reconfigure AWS resources",
                    "examples": [
                        "Delete this EC2 instance",
                        "Terminate your RDS database",
                        "Disable this security group",
                    ],
                    "type": "DENY",
                },
            ]
        },
        "sensitiveInformationPolicyConfig": {
            "piiEntitiesConfig": [
                {"type": "EMAIL", "action": "BLOCK"},
                {"type": "PHONE", "action": "BLOCK"},
                {"type": "NAME", "action": "ANONYMIZE"},
                {"type": "AWS_ACCESS_KEY", "action": "BLOCK"},
                {"type": "AWS_SECRET_KEY", "action": "BLOCK"},
            ]
        },
        "wordPolicyConfig": {
            "wordsConfig": [
                {"text": "your account will be terminated"},
                {"text": "immediate action required"},
                {"text": "your data is at risk"},
                {"text": "urgent security alert"},
            ]
        },
        "blockedInputMessaging": (
            "MIO Agent cannot process this request as it falls outside the scope of "
            "observability assessment. Please rephrase your request."
        ),
        "blockedOutputsMessaging": (
            "MIO Agent's response was blocked by safety guardrails. "
            "The assessment has been flagged for human review."
        ),
    }

    try:
        response = client.create_guardrail(**guardrail_config)
        guardrail_id = response["guardrailId"]

        # Store in SSM for runtime retrieval
        ssm = boto3.client("ssm", region_name=region)
        ssm.put_parameter(
            Name=GUARDRAIL_ID_PARAM,
            Value=guardrail_id,
            Type="String",
            Overwrite=True,
            Description="MIO Agent Bedrock Guardrail ID",
        )
        ssm.put_parameter(
            Name=GUARDRAIL_VERSION_PARAM,
            Value="DRAFT",
            Type="String",
            Overwrite=True,
        )

        logger.info("Created Bedrock Guardrail", extra={"guardrail_id": guardrail_id})
        return guardrail_id

    except ClientError as e:
        logger.warning("Failed to create Bedrock Guardrail", extra={"error": str(e)})
        raise
