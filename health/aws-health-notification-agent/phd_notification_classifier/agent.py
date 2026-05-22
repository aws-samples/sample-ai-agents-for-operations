# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""AWS Health Notification Classifier — Agent entry point.

Runs on Amazon Bedrock AgentCore Runtime. Creates a Strands Agent with
all tool functions and streams the response back to the runtime.

GenAI Risk Assessment:
    This is a HIGH-RISK GenAI use case: the agent classifies AWS Health events
    and can execute remediation actions (e.g., EKS cluster upgrades) on production
    infrastructure. The following controls are in place:

    1. MCP Server Allowlist:
       - Only `awslabs.eks-mcp-server` is approved (Apache 2.0, AWS Labs).
       - discover_mcp_tools() only connects when EKS_MCP_ENABLED=true.
       - No dynamic/untrusted MCP server discovery. See SECURITY.md "3rd Party
         Service Approvals" for legal review documentation.

    2. Human-in-the-Loop for Remediation:
       - The agent NEVER executes remediation autonomously.
       - Remediation mode is only entered when the Lambda passes a payload with
         `remediation_action` key — which requires a valid, single-use DynamoDB
         approval token generated via the two-step human approval flow
         (GET confirmation page → POST execute).
       - The approval token is generated only after a human clicks "Approve" in
         an SES email or Slack interactive message.
       - See SECURITY.md mitigations M-003, M-005, M-016.

    3. Classification (non-destructive path):
       - Classification results are published to SNS for human review.
       - No infrastructure changes occur without explicit human approval.
       - Output validated against JSON schema before downstream processing.

    4. Risk Classification:
       - Production accounts with BREAKING_CHANGE: requires human approval
       - All remediation actions: gated behind cryptographic approval token
       - Non-production/informational: notification only, no remediation
"""

from __future__ import annotations

import json
import logging
import os

from strands import Agent
from bedrock_agentcore import BedrockAgentCoreApp

from phd_notification_classifier.gateway_tools import discover_mcp_tools
from phd_notification_classifier.prompts import SYSTEM_PROMPT, build_remediation_prompt
from phd_notification_classifier.tools.account_context import get_account_context
from phd_notification_classifier.tools.application_context import get_account_application_trust_store
from phd_notification_classifier.tools.consolidation import consolidate_notifications
from phd_notification_classifier.tools.impact_analyzer import analyze_impact
from phd_notification_classifier.tools.cost_estimator import estimate_cost
from phd_notification_classifier.tools.eks_cluster import describe_eks_cluster, upgrade_eks_cluster
from phd_notification_classifier.tools.sns_notifier import publish_to_sns

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# --- MCP tool discovery (optional, direct EKS MCP server) ---
mcp_tools = discover_mcp_tools()

# --- Build tool list ---
local_tools = [
    get_account_context,
    consolidate_notifications,
    analyze_impact,
    estimate_cost,
    describe_eks_cluster,
    upgrade_eks_cluster,
    publish_to_sns,
]

# Only include the hardcoded trust store tool if no MCP tools are available
if not mcp_tools:
    local_tools.append(get_account_application_trust_store)

all_tools = local_tools + mcp_tools

DEFAULT_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"


def _load_system_prompt() -> str:
    """Load system prompt from S3 if configured, otherwise use embedded default.

    Reads from the S3 object specified by SYSTEM_PROMPT_S3_BUCKET and
    SYSTEM_PROMPT_S3_KEY environment variables. Falls back to the embedded
    SYSTEM_PROMPT from prompts.py if S3 is not configured or read fails.
    """
    bucket = os.environ.get("SYSTEM_PROMPT_S3_BUCKET")
    key = os.environ.get("SYSTEM_PROMPT_S3_KEY", "prompts/system_prompt.txt")

    if not bucket:
        logger.info("SYSTEM_PROMPT_S3_BUCKET not set, using embedded system prompt")
        return SYSTEM_PROMPT

    try:
        import boto3
        region = os.environ.get("AWS_REGION", "eu-west-1")
        s3 = boto3.client("s3", region_name=region)
        response = s3.get_object(Bucket=bucket, Key=key)
        prompt_text = response["Body"].read().decode("utf-8")
        logger.info("System prompt loaded from s3://%s/%s (%d chars)", bucket, key, len(prompt_text))
        return prompt_text
    except Exception as exc:
        logger.warning("Failed to load system prompt from S3, using embedded default: %s", exc)
        return SYSTEM_PROMPT


_system_prompt = _load_system_prompt()

agent = Agent(
    model=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
    system_prompt=_system_prompt,
    tools=all_tools,
)

ERROR_RESPONSE = json.dumps({"status": "error", "error": ""})

ALLOWED_STATUSES = {"open", "upcoming"}


def filter_by_status(events: list[dict]) -> list[dict]:
    """Filter health events to include only open or upcoming statuses.

    Events with status codes other than "open" or "upcoming" (e.g. "closed",
    "resolved", "unknown") are silently excluded.
    """
    return [e for e in events if e.get("statusCode", "").lower() in ALLOWED_STATUSES]


def build_prompt(payload: dict) -> str:
    """Build the agent prompt from the health event payload.

    Extracts the health event data and an optional ``limit`` parameter.
    Filters events to only include open/upcoming statuses and applies the
    limit cap when provided.
    """
    health_event = payload.get("health_event", payload)
    limit = payload.get("limit")

    # If the payload contains a list of events, filter by status and apply limit
    if isinstance(health_event, list):
        health_event = filter_by_status(health_event)
        if limit and int(limit) > 0:
            health_event = health_event[: int(limit)]
    elif isinstance(health_event, dict) and "statusCode" in health_event:
        # Single event — filter by status
        if health_event.get("statusCode", "").lower() not in ALLOWED_STATUSES:
            health_event = []

    prompt = (
        "Process the following AWS Health event payload and classify it:\n\n"
        + json.dumps(health_event, default=str)
    )

    if limit and int(limit) > 0:
        prompt += f"\n\nLimit processing to {int(limit)} notifications."

    return prompt


@app.entrypoint
async def classify_notifications(payload):
    """Receive a health event payload and stream the agent response."""
    # Validate incoming payload
    if payload is None:
        yield json.dumps({"status": "error", "error": "Payload is None"})
        return

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as exc:
            yield json.dumps({
                "status": "error",
                "error": f"Failed to parse health event payload: {exc}",
            })
            return

    if not isinstance(payload, dict):
        yield json.dumps({
            "status": "error",
            "error": "Failed to parse health event payload: expected a JSON object",
        })
        return

    # Check for remediation mode
    if isinstance(payload, dict) and "remediation_action" in payload:
        remediation_prompt = build_remediation_prompt(payload["remediation_action"])
        async for event in agent.stream_async(remediation_prompt):
            if "result" in event:
                result = event["result"]
                message = result.message if hasattr(result, "message") else {}
                content = message.get("content", []) if isinstance(message, dict) else []
                text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and "text" in c]
                final_text = "\n".join(text_parts)
                yield final_text
        return

    try:
        prompt = build_prompt(payload)
    except Exception as exc:
        yield json.dumps({
            "status": "error",
            "error": f"Failed to parse health event payload: {exc}",
        })
        return

    async for event in agent.stream_async(prompt):
        # Only yield the final result, not intermediate streaming events
        if "result" in event:
            result = event["result"]
            # Extract the final message text from the AgentResult
            message = result.message if hasattr(result, "message") else {}
            content = message.get("content", []) if isinstance(message, dict) else []
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and "text" in c]
            final_text = "\n".join(text_parts)
            yield final_text


if __name__ == "__main__":
    app.run()
