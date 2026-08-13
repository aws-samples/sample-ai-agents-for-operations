# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Integration test for the agent module.

Exercises the integration between agent.py, tools/, helpers/, and prompts/
with external AWS dependencies (Bedrock LLM, boto3) mocked. Verifies:
- The agent handler correctly invokes tools when the model requests them
- Tool results are passed back to the model
- Final text response is returned to the caller
- The system prompt is loaded and passed to the model
"""

import importlib
from unittest.mock import patch

import pytest

import helpers.snapshot as helpers_snapshot


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before each test."""
    helpers_snapshot._snapshot_cache = None
    yield
    helpers_snapshot._snapshot_cache = None


def _make_stream_events_for_tool_call(tool_name, tool_input, tool_use_id="call-1"):
    """Create stream events that simulate the model requesting a tool call."""
    async def stream(*args, **kwargs):
        yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {"toolUseId": tool_use_id, "name": tool_name}}}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": str(tool_input)}}}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {"usage": {"inputTokens": 100, "outputTokens": 50}}}
    return stream


def _make_stream_events_for_text(text):
    """Create stream events that simulate the model returning a text response."""
    async def stream(*args, **kwargs):
        yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {"text": ""}}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}}
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 200, "outputTokens": 80}}}
    return stream


class FakeBedrockModel:
    """Fake model that returns predetermined stream responses.

    Satisfies the strands Model ABC interface with the minimum required
    to run the Agent loop.
    """

    def __init__(self, stream_sequences):
        """stream_sequences: list of async generator factories, consumed in order."""
        self._streams = iter(stream_sequences)

    @property
    def stateful(self):
        return False

    @property
    def context_window_limit(self):
        return 200000

    def get_config(self):
        return {"model_id": "us.anthropic.claude-sonnet-4-6"}

    def update_config(self, **kwargs):
        pass

    def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        return next(self._streams)(messages, tool_specs=tool_specs, system_prompt=system_prompt, **kwargs)

    def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("Not used in this test")


class TestAgentToolIntegration:
    """Test that the agent handler integrates correctly with tools."""

    def test_get_customer_profile_invoked_and_result_returned(self):
        """Model requests get_customer_profile → tool runs → model gets result → final text."""
        # Seed snapshot so get_customer_profile returns real data
        helpers_snapshot._snapshot_cache = {
            "PK": "customer-profile",
            "SK": "latest",
            "assembled_at": "2025-06-19T10:00:00Z",
            "regions_scanned": ["us-east-1"],
            "models": [
                {
                    "model_id": "anthropic.claude-sonnet-4-6-v1:0",
                    "display_name": "Claude Sonnet 4.6",
                    "provider": "Anthropic",
                    "active_patterns": [{
                        "pattern_type": "on-demand",
                        "cw_model_id": "anthropic.claude-sonnet-4-6-v1:0",
                        "geography": None,
                        "regions": ["us-east-1"],
                        "quota_limits": {"rpm_limit": 100, "tpm_limit": 100000},
                        "usage_summary": {"invocations_24h": 500},
                    }],
                    "app_profiles": [],
                }
            ],
        }

        # Model first requests get_customer_profile, then produces final text
        fake_model = FakeBedrockModel([
            _make_stream_events_for_tool_call("get_customer_profile", {}),
            _make_stream_events_for_text("You have Claude Sonnet 4.6 active in us-east-1."),
        ])

        agent_mod = importlib.import_module("agent")

        with patch("strands.models.bedrock.BedrockModel", return_value=fake_model):
            response = agent_mod.agent_handler({"prompt": "What models do I have?"})

        assert "result" in response
        assert "Claude Sonnet 4.6" in response["result"]

    def test_system_prompt_passed_to_model(self):
        """Verify the system prompt from prompts/system_prompt.md reaches the model."""
        helpers_snapshot._snapshot_cache = None

        captured_system_prompt = []

        original_stream = _make_stream_events_for_text("Hello!")

        async def capturing_stream(*args, system_prompt=None, **kwargs):
            captured_system_prompt.append(system_prompt)
            async for event in original_stream(*args, system_prompt=system_prompt, **kwargs):
                yield event

        fake_model = FakeBedrockModel([capturing_stream])

        agent_mod = importlib.import_module("agent")

        with patch("strands.models.bedrock.BedrockModel", return_value=fake_model):
            agent_mod.agent_handler({"prompt": "hi"})

        assert len(captured_system_prompt) >= 1
        assert captured_system_prompt[0] is not None
        assert len(captured_system_prompt[0]) > 100

    def test_include_trace_captures_tool_calls(self):
        """When include_trace=True, response includes tool call trace."""
        helpers_snapshot._snapshot_cache = {
            "PK": "customer-profile", "SK": "latest",
            "assembled_at": "2025-06-19", "regions_scanned": [],
            "models": [],
        }

        fake_model = FakeBedrockModel([
            _make_stream_events_for_tool_call("get_customer_profile", {}),
            _make_stream_events_for_text("No models found."),
        ])

        agent_mod = importlib.import_module("agent")

        with patch("strands.models.bedrock.BedrockModel", return_value=fake_model):
            response = agent_mod.agent_handler({
                "prompt": "Show my setup",
                "include_trace": True,
            })

        assert "trace" in response
        tool_calls = response["trace"]["tool_calls"]
        assert any(tc["name"] == "get_customer_profile" for tc in tool_calls)
