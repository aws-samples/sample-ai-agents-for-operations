# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for the agent handler entrypoint (src/agent.py).

Tests the orchestration logic: tool wiring, state reset, payload parsing,
region prefix logic, response extraction, post-processing, and trace inclusion.

All external dependencies (strands.Agent, BedrockModel, boto3) are mocked.
"""

import importlib
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def agent_module():
    """Import the agent module with all heavy deps mocked."""
    import sys

    # Ensure fresh import
    for mod in list(sys.modules):
        if mod in ("agent",) or mod.startswith("tools.draft_quota_increase_request"):
            del sys.modules[mod]

    return importlib.import_module("agent")


@pytest.fixture()
def mock_agent_class(agent_module):
    """Patch strands.Agent inside agent_handler and return the mock class."""
    with patch("strands.Agent") as mock_cls:
        mock_result = MagicMock()
        mock_result.message = {"content": [{"text": "Hello from agent"}]}
        mock_cls.return_value.return_value = mock_result
        mock_cls.return_value.messages = []
        yield mock_cls


@pytest.fixture()
def mock_bedrock_model(agent_module):
    """Patch BedrockModel inside agent_handler."""
    with patch("strands.models.bedrock.BedrockModel") as mock_cls:
        yield mock_cls


class TestToolWiring:
    """Verify agent_handler passes all 9 tools to the Agent constructor."""

    def test_all_nine_tools_registered(self, agent_module, mock_agent_class, mock_bedrock_model):
        agent_module.agent_handler({"prompt": "hello"})

        tools_arg = mock_agent_class.call_args.kwargs["tools"]
        assert len(tools_arg) == 9

    def test_tool_names_match_expected_set(self, agent_module, mock_agent_class, mock_bedrock_model):
        agent_module.agent_handler({"prompt": "hello"})

        tools_arg = mock_agent_class.call_args.kwargs["tools"]
        tool_names = set()
        for t in tools_arg:
            # Prefer tool_spec["name"] (Strands canonical), fall back to __name__
            spec = getattr(t, "tool_spec", None)
            if spec and isinstance(spec, dict):
                tool_names.add(spec["name"])
            else:
                name = getattr(t, "__name__", None) or getattr(t, "name", None) or str(t)
                tool_names.add(name.split(".")[-1])
        expected = {
            "get_customer_profile",
            "get_bedrock_model_quotas",
            "get_bedrock_model_invocation_metrics",
            "list_available_bedrock_models",
            "list_active_bedrock_models",
            "list_active_inference_profiles",
            "check_quota_utilization",
            "draft_quota_increase_request",
            "submit_quota_increase_case",
        }
        assert tool_names == expected

    def test_system_prompt_passed(self, agent_module, mock_agent_class, mock_bedrock_model):
        agent_module.agent_handler({"prompt": "hello"})

        system_prompt = mock_agent_class.call_args.kwargs["system_prompt"]
        assert isinstance(system_prompt, str)
        assert len(system_prompt) > 0


class TestStateReset:
    """Verify per-invocation state is cleared at the start of each call."""

    def test_snapshot_cache_reset(self, agent_module, mock_agent_class, mock_bedrock_model):
        import helpers.snapshot as snap_mod
        snap_mod._snapshot_cache = {"stale": "data"}

        agent_module.agent_handler({"prompt": "hello"})

        assert snap_mod._snapshot_cache is None

    def test_draft_data_reset(self, agent_module, mock_agent_class, mock_bedrock_model):
        import tools.draft_quota_increase_request as draft_mod
        draft_mod._last_draft_data = {"stale": "draft"}

        agent_module.agent_handler({"prompt": "hello"})

        assert draft_mod._last_draft_data is None


class TestPayloadParsing:
    """Verify payload fields are extracted correctly."""

    def test_prompt_passed_to_agent(self, agent_module, mock_agent_class, mock_bedrock_model):
        agent_module.agent_handler({"prompt": "What are my quotas?"})

        mock_agent_class.return_value.assert_called_once_with("What are my quotas?")

    def test_default_prompt_when_missing(self, agent_module, mock_agent_class, mock_bedrock_model):
        agent_module.agent_handler({})

        mock_agent_class.return_value.assert_called_once_with("Hello, how can I help you?")


class TestRegionPrefixLogic:
    """Verify cross-region inference model prefix selection."""

    def test_eu_region_uses_eu_prefix(self, agent_module, mock_agent_class, mock_bedrock_model):
        with patch.object(agent_module, "AGENTCORE_REGION", "eu-west-1"):
            agent_module.agent_handler({"prompt": "hi"})

        model_id = mock_bedrock_model.call_args.kwargs["model_id"]
        assert model_id == "eu.anthropic.claude-sonnet-4-6"

    def test_ap_region_uses_apac_prefix(self, agent_module, mock_agent_class, mock_bedrock_model):
        with patch.object(agent_module, "AGENTCORE_REGION", "ap-southeast-1"):
            agent_module.agent_handler({"prompt": "hi"})

        model_id = mock_bedrock_model.call_args.kwargs["model_id"]
        assert model_id == "apac.anthropic.claude-sonnet-4-6"

    def test_us_region_uses_us_prefix(self, agent_module, mock_agent_class, mock_bedrock_model):
        with patch.object(agent_module, "AGENTCORE_REGION", "us-east-1"):
            agent_module.agent_handler({"prompt": "hi"})

        model_id = mock_bedrock_model.call_args.kwargs["model_id"]
        assert model_id == "us.anthropic.claude-sonnet-4-6"

    def test_none_region_defaults_to_eu(self, agent_module, mock_agent_class, mock_bedrock_model):
        with patch.object(agent_module, "AGENTCORE_REGION", None):
            agent_module.agent_handler({"prompt": "hi"})

        model_id = mock_bedrock_model.call_args.kwargs["model_id"]
        assert model_id == "eu.anthropic.claude-sonnet-4-6"


class TestSessionManager:
    """Verify session manager conditional initialization."""

    def test_session_manager_created_when_all_fields_present(self, agent_module, mock_agent_class, mock_bedrock_model):
        with patch.object(agent_module, "AGENTCORE_MEMORY_ID", "mem-123"), \
             patch("bedrock_agentcore.memory.integrations.strands.config.AgentCoreMemoryConfig"), \
             patch("bedrock_agentcore.memory.integrations.strands.session_manager.AgentCoreMemorySessionManager"):
            agent_module.agent_handler({
                "prompt": "hi",
                "session_id": "thread-abc",
                "actor_id": "user-xyz",
            })

        session_manager_arg = mock_agent_class.call_args.kwargs["session_manager"]
        assert session_manager_arg is not None

    def test_session_manager_none_when_memory_id_missing(self, agent_module, mock_agent_class, mock_bedrock_model):
        with patch.object(agent_module, "AGENTCORE_MEMORY_ID", None):
            agent_module.agent_handler({
                "prompt": "hi",
                "session_id": "thread-abc",
                "actor_id": "user-xyz",
            })

        session_manager_arg = mock_agent_class.call_args.kwargs["session_manager"]
        assert session_manager_arg is None

    def test_session_manager_none_when_session_id_missing(self, agent_module, mock_agent_class, mock_bedrock_model):
        with patch.object(agent_module, "AGENTCORE_MEMORY_ID", "mem-123"):
            agent_module.agent_handler({
                "prompt": "hi",
                "actor_id": "user-xyz",
            })

        session_manager_arg = mock_agent_class.call_args.kwargs["session_manager"]
        assert session_manager_arg is None


class TestResponseExtraction:
    """Verify response text is extracted from the message structure."""

    def test_extracts_text_from_content_blocks(self, agent_module, mock_agent_class, mock_bedrock_model):
        mock_result = MagicMock()
        mock_result.message = {
            "content": [
                {"text": "Part 1"},
                {"text": "Part 2"},
            ]
        }
        mock_agent_class.return_value.return_value = mock_result
        mock_agent_class.return_value.messages = []

        response = agent_module.agent_handler({"prompt": "hi"})

        assert "Part 1" in response["result"]
        assert "Part 2" in response["result"]

    def test_falls_back_to_str_when_no_content_key(self, agent_module, mock_agent_class, mock_bedrock_model):
        mock_result = MagicMock()
        mock_result.message = "plain string response"
        mock_agent_class.return_value.return_value = mock_result
        mock_agent_class.return_value.messages = []

        response = agent_module.agent_handler({"prompt": "hi"})

        assert "plain string response" in response["result"]


class TestPostProcessing:
    """Verify _append_draft_actions is called on the response."""

    def test_draft_actions_appended_when_draft_exists(self, agent_module, mock_agent_class, mock_bedrock_model):
        """Verify _append_draft_actions is called. We simulate what happens when
        draft_quota_increase_request sets _last_draft_data during the agent invocation
        by using a side_effect on the mock agent call."""
        import tools.draft_quota_increase_request as draft_mod

        draft_data = {
            "subject": "Test",
            "case_body": "body content here",
            "cli_command_file": "aws support create-case ...",
            "cli_command_inline": "aws support create-case ...",
            "links": {"support_console": "https://console.aws.amazon.com", "service_quotas": "https://console.aws.amazon.com/sq"},
            "note": "Requires Business plan",
        }

        def _agent_call_side_effect(prompt):
            # Simulate tool setting draft data during invocation
            draft_mod._last_draft_data = draft_data
            result = MagicMock()
            result.message = {"content": [{"text": "Draft ready."}]}
            return result

        mock_agent_class.return_value.side_effect = _agent_call_side_effect
        mock_agent_class.return_value.messages = []

        response = agent_module.agent_handler({"prompt": "hi"})

        assert "body content here" in response["result"]


class TestTraceInclusion:
    """Verify include_trace controls trace output."""

    def test_no_trace_by_default(self, agent_module, mock_agent_class, mock_bedrock_model):
        response = agent_module.agent_handler({"prompt": "hi"})

        assert "trace" not in response

    def test_trace_included_when_requested(self, agent_module, mock_agent_class, mock_bedrock_model):
        mock_agent_class.return_value.messages = [
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"name": "get_customer_profile", "input": {}}}
                ],
            }
        ]

        response = agent_module.agent_handler({"prompt": "hi", "include_trace": True})

        assert "trace" in response
        assert response["trace"]["tool_calls"] == [
            {"name": "get_customer_profile", "input": {}}
        ]


class TestExtractToolCalls:
    """Unit tests for the _extract_tool_calls helper."""

    def test_empty_messages(self, agent_module):
        assert agent_module._extract_tool_calls([]) == []
        assert agent_module._extract_tool_calls(None) == []

    def test_ignores_non_assistant_messages(self, agent_module):
        messages = [{"role": "user", "content": [{"toolUse": {"name": "x", "input": {}}}]}]
        assert agent_module._extract_tool_calls(messages) == []

    def test_extracts_multiple_tool_calls(self, agent_module):
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"name": "tool_a", "input": {"k": "v"}}},
                    {"toolUse": {"name": "tool_b", "input": {}}},
                ],
            }
        ]
        result = agent_module._extract_tool_calls(messages)
        assert result == [
            {"name": "tool_a", "input": {"k": "v"}},
            {"name": "tool_b", "input": {}},
        ]

    def test_skips_non_dict_blocks(self, agent_module):
        messages = [
            {
                "role": "assistant",
                "content": [
                    "just a string",
                    {"text": "no tool use here"},
                    {"toolUse": {"name": "real_tool", "input": {}}},
                ],
            }
        ]
        result = agent_module._extract_tool_calls(messages)
        assert result == [{"name": "real_tool", "input": {}}]


class TestLoadSystemPrompt:
    """Verify system prompt loading."""

    def test_loads_from_prompts_directory(self, agent_module):
        prompt = agent_module._load_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100
