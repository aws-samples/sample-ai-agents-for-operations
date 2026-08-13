# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Bedrock Quota and Utilization Agent — AgentCore Runtime entrypoint."""

import logging
from pathlib import Path

from bedrock_agentcore import BedrockAgentCoreApp

from config import AGENTCORE_MEMORY_ID, AGENTCORE_REGION
from tools import (
    get_customer_profile,
    get_bedrock_model_quotas,
    get_bedrock_model_invocation_metrics,
    list_available_bedrock_models,
    list_active_bedrock_models,
    list_active_inference_profiles,
    check_quota_utilization,
    draft_quota_increase_request,
    submit_quota_increase_case,
)
from tools.draft_quota_increase_request import _append_draft_actions
import tools.draft_quota_increase_request as _draft_module
import helpers.snapshot as _snapshot_module

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()


def _load_system_prompt() -> str:
    """Load the system prompt from the prompts directory."""
    prompt_path = Path(__file__).parent / "prompts" / "system_prompt.md"
    return prompt_path.read_text()


def _extract_tool_calls(messages: list) -> list[dict]:
    """Extract tool-call events from a Strands conversation message list."""
    calls: list[dict] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        if message.get("role") != "assistant":
            continue
        for block in message.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            tool_use = block.get("toolUse")
            if not isinstance(tool_use, dict):
                continue
            calls.append({
                "name": tool_use.get("name", ""),
                "input": tool_use.get("input", {}),
            })
    return calls


@app.entrypoint
def agent_handler(payload: dict) -> dict:
    """AgentCore entrypoint handler.

    Args:
        payload: Dict containing:
            - prompt: User's message
            - session_id: Optional session ID for conversation continuity
            - actor_id: Optional actor ID for user identification
            - include_trace: Optional bool for eval framework tool-call tracing
    """
    from strands import Agent
    from strands.models.bedrock import BedrockModel

    prompt = payload.get("prompt", "Hello, how can I help you?")
    session_id = payload.get("session_id")
    actor_id = payload.get("actor_id")
    include_trace = bool(payload.get("include_trace", False))

    # Reset per-invocation state
    _draft_module._last_draft_data = None
    _snapshot_module._snapshot_cache = None

    # Configure session manager if Memory is configured and session info provided
    session_manager = None
    if AGENTCORE_MEMORY_ID and session_id and actor_id:
        try:
            from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
            from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

            config = AgentCoreMemoryConfig(
                memory_id=AGENTCORE_MEMORY_ID,
                session_id=session_id,
                actor_id=actor_id
            )
            session_manager = AgentCoreMemorySessionManager(
                agentcore_memory_config=config,
                region_name=AGENTCORE_REGION
            )
        except Exception as e:
            logger.warning(f"Could not initialize session manager: {e}", exc_info=True)

    # Determine cross-region inference prefix based on region
    _region = AGENTCORE_REGION or "eu-west-1"
    if _region.startswith("eu-"):
        _model_prefix = "eu."
    elif _region.startswith("ap-"):
        _model_prefix = "apac."
    else:
        _model_prefix = "us."

    model = BedrockModel(
        model_id=f"{_model_prefix}anthropic.claude-sonnet-4-6",
        region_name=_region,
    )

    agent = Agent(
        model=model,
        tools=[
            get_customer_profile,
            get_bedrock_model_quotas,
            get_bedrock_model_invocation_metrics,
            list_available_bedrock_models,
            list_active_bedrock_models,
            list_active_inference_profiles,
            check_quota_utilization,
            draft_quota_increase_request,
            submit_quota_increase_case,
        ],
        session_manager=session_manager,
        system_prompt=_load_system_prompt(),
    )

    result = agent(prompt)

    # Extract text content from the message structure
    message = result.message
    if isinstance(message, dict) and 'content' in message:
        text_parts = []
        for block in message.get('content', []):
            if isinstance(block, dict) and 'text' in block:
                text_parts.append(block['text'])
        response_text = '\n'.join(text_parts)
    else:
        response_text = str(message)

    # Post-processing: append actionable content from draft_quota_increase_request
    response_text = _append_draft_actions(response_text)

    response = {"result": response_text}
    if include_trace:
        response["trace"] = {"tool_calls": _extract_tool_calls(agent.messages)}
    return response


if __name__ == "__main__":
    app.run()
