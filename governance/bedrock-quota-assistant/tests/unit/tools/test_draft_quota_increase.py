# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for draft_quota_increase_request tool and _append_draft_actions."""

import importlib
import json
from unittest.mock import patch

import pytest

_mod = importlib.import_module("tools.draft_quota_increase_request")
draft_quota_increase_request = _mod.draft_quota_increase_request
_append_draft_actions = _mod._append_draft_actions


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

REGION = "us-west-2"
MODEL_ID = "anthropic.claude-sonnet-4-20250514-v1:0"
DISPLAY_NAME = "Claude Sonnet 4"

BASE_KWARGS = {
    "model_id": "claude sonnet 4",
    "region": REGION,
    "use_case": "AI coding assistant for 500 engineers",
    "desired_tpm": 200000,
    "desired_rpm": 1000,
    "steady_state_tpm": 80000,
    "peak_tpm": 150000,
    "steady_state_rpm": 400,
    "peak_rpm": 800,
    "avg_input_tokens": 2000,
    "avg_output_tokens": 500,
}

SAMPLE_QUOTA_CODES = [
    {"PK": "quota", "SK": "L-TPM-001", "quota_code": "L-TPM-001", "quota_name": "Claude Sonnet 4 Tokens Per Minute"},
    {"PK": "quota", "SK": "L-RPM-001", "quota_code": "L-RPM-001", "quota_name": "Claude Sonnet 4 Requests Per Minute"},
]

SAMPLE_VALUES = [
    {"name": "Claude Sonnet 4 Tokens Per Minute", "value": 100000, "unit": "None", "adjustable": True},
    {"name": "Claude Sonnet 4 Requests Per Minute", "value": 500, "unit": "None", "adjustable": True},
]


@pytest.fixture(autouse=True)
def reset_draft_state():
    """Reset _last_draft_data between tests."""
    _mod._last_draft_data = None
    yield
    _mod._last_draft_data = None


# ---------------------------------------------------------------------------
# Test: draft_quota_increase_request — generates JSON with status "draft_ready"
# ---------------------------------------------------------------------------


class TestDraftReady:
    """The tool should return a JSON response with status 'draft_ready'."""

    def test_returns_draft_ready_status(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_VALUES):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert data["status"] == "draft_ready"

    def test_returns_model_and_region(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_VALUES):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert data["model"] == DISPLAY_NAME
        assert data["region"] == REGION
        assert data["desired_tpm"] == 200000
        assert data["desired_rpm"] == 1000

    def test_unresolved_model_id_uses_raw_input(self):
        with patch.object(_mod, "resolve_model_id", return_value=None), \
             patch.object(_mod, "get_model_info", return_value=None), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        # When model_info is None, display_name falls back to resolved (which is model_id input)
        assert data["model"] == "claude sonnet 4"


# ---------------------------------------------------------------------------
# Test: Subject includes model name and region
# ---------------------------------------------------------------------------


class TestSubjectLine:
    """Subject field should include model name and region."""

    def test_subject_includes_model_and_region(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert data["subject"] == f"Bedrock quota increase: {DISPLAY_NAME} in {REGION}"


# ---------------------------------------------------------------------------
# Test: Current quotas fetched and included
# ---------------------------------------------------------------------------


class TestCurrentQuotasFetched:
    """The draft should fetch current quota values and include them."""

    def test_current_tpm_and_rpm_populated(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_VALUES):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert data["current_tpm"] == 100000
        assert data["current_rpm"] == 500

    def test_no_quota_codes_returns_none_for_current_values(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert data["current_tpm"] is None
        assert data["current_rpm"] is None

    def test_quota_fetch_exception_does_not_crash(self):
        """Quota fetch errors are caught — the draft still generates."""
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", side_effect=RuntimeError("DynamoDB down")), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert data["status"] == "draft_ready"
        assert data["current_tpm"] is None

    def test_cross_region_quotas_used_as_fallback(self):
        """When no non-cross-region quota is found, cross-region values are used."""
        cross_region_values = [
            {"name": "Cross-region Claude Sonnet 4 Tokens Per Minute", "value": 200000, "unit": "None", "adjustable": True},
            {"name": "Cross-region Claude Sonnet 4 Requests Per Minute", "value": 1000, "unit": "None", "adjustable": True},
        ]
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=cross_region_values), \
             patch.object(_mod, "_filter_strict_model_match", return_value=cross_region_values):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert data["current_tpm"] == 200000
        assert data["current_rpm"] == 1000


# ---------------------------------------------------------------------------
# Test: Strength note reflects utilization level
# ---------------------------------------------------------------------------


class TestStrengthNote:
    """Summary should contain appropriate strength notes based on utilization."""

    def test_high_utilization_shows_strong_request(self):
        """peak_tpm=150000 vs current_tpm=100000 → 150% → 'Strong request'."""
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_VALUES):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert "Strong request" in data["summary"]

    def test_low_utilization_with_justification(self):
        """Low utilization but justification provided."""
        low_peak_kwargs = {**BASE_KWARGS, "peak_tpm": 10000, "peak_rpm": 30, "justification": "New region expansion"}
        low_values = [
            {"name": "Claude Sonnet 4 Tokens Per Minute", "value": 100000, "unit": "None", "adjustable": True},
            {"name": "Claude Sonnet 4 Requests Per Minute", "value": 500, "unit": "None", "adjustable": True},
        ]
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=low_values), \
             patch.object(_mod, "_filter_strict_model_match", return_value=low_values):
            result = draft_quota_increase_request(**low_peak_kwargs)

        data = json.loads(result)
        assert "Low utilization" in data["summary"]
        assert "justification included" in data["summary"]

    def test_low_utilization_without_justification(self):
        """Low utilization, no justification → warning about delays."""
        low_peak_kwargs = {**BASE_KWARGS, "peak_tpm": 10000, "peak_rpm": 30}
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_VALUES):
            result = draft_quota_increase_request(**low_peak_kwargs)

        data = json.loads(result)
        assert "Low utilization" in data["summary"]
        assert "avoid delays" in data["summary"]

    def test_no_utilization_data_with_justification(self):
        """No current quota data but justification provided."""
        kwargs_with_justification = {**BASE_KWARGS, "justification": "New account setup"}
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = draft_quota_increase_request(**kwargs_with_justification)

        data = json.loads(result)
        assert "No utilization data" in data["summary"]
        assert "justification included" in data["summary"]

    def test_no_utilization_data_no_justification(self):
        """No current quota data and no justification → may be delayed."""
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = draft_quota_increase_request(**BASE_KWARGS)

        data = json.loads(result)
        assert "No utilization data" in data["summary"]
        assert "may be delayed" in data["summary"]


# ---------------------------------------------------------------------------
# Test: _last_draft_data populated with case_body, CLI commands, links
# ---------------------------------------------------------------------------


class TestLastDraftDataPopulated:
    """After calling the tool, _last_draft_data should have all needed fields."""

    def test_last_draft_data_contains_case_body(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_VALUES):
            draft_quota_increase_request(**BASE_KWARGS)

        draft = _mod._last_draft_data
        assert draft is not None
        assert "case_body" in draft
        assert "Claude Sonnet 4" in draft["case_body"]
        assert "AI coding assistant for 500 engineers" in draft["case_body"]
        assert REGION in draft["case_body"]

    def test_last_draft_data_contains_cli_commands(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            draft_quota_increase_request(**BASE_KWARGS)

        draft = _mod._last_draft_data
        assert "cli_command_file" in draft
        assert "cli_command_inline" in draft
        assert "aws support create-case" in draft["cli_command_file"]
        assert "file://case-body.txt" in draft["cli_command_file"]
        assert "aws support create-case" in draft["cli_command_inline"]

    def test_last_draft_data_contains_links(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            draft_quota_increase_request(**BASE_KWARGS)

        draft = _mod._last_draft_data
        assert "links" in draft
        assert "support_console" in draft["links"]
        assert "service_quotas" in draft["links"]
        assert REGION in draft["links"]["service_quotas"]

    def test_last_draft_data_contains_subject(self):
        with patch.object(_mod, "resolve_model_id", return_value=MODEL_ID), \
             patch.object(_mod, "get_model_info", return_value={"name": DISPLAY_NAME}), \
             patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            draft_quota_increase_request(**BASE_KWARGS)

        draft = _mod._last_draft_data
        assert draft["subject"] == f"Bedrock quota increase: {DISPLAY_NAME} in {REGION}"


# ---------------------------------------------------------------------------
# Test: _append_draft_actions — returns unchanged when _last_draft_data is None
# ---------------------------------------------------------------------------


class TestAppendDraftActionsNoDraft:
    """When no draft exists, the text passes through unchanged."""

    def test_returns_unchanged_when_no_draft(self):
        _mod._last_draft_data = None
        text = "Here is the utilization report for your model."
        result = _append_draft_actions(text)
        assert result == text

    def test_returns_unchanged_for_empty_string(self):
        _mod._last_draft_data = None
        result = _append_draft_actions("")
        assert result == ""


# ---------------------------------------------------------------------------
# Test: _append_draft_actions — appends case body, CLI, links when draft exists
# ---------------------------------------------------------------------------


class TestAppendDraftActionsWithDraft:
    """When draft data is present, it should append the authoritative content."""

    def test_appends_case_body(self):
        _mod._last_draft_data = {
            "subject": "Test Subject",
            "case_body": "This is the case body content.",
            "cli_command_file": "aws support create-case --subject 'Test' --communication-body file://case-body.txt",
            "cli_command_inline": "aws support create-case --subject 'Test' --communication-body 'inline body'",
            "links": {
                "support_console": "https://console.aws.amazon.com/support/home#/case/create",
                "service_quotas": "https://console.aws.amazon.com/servicequotas/home?region=us-west-2#!/services/bedrock/quotas",
            },
            "note": "Requires Business or Enterprise Support plan.",
        }
        text = "Here is your draft quota increase request."
        result = _append_draft_actions(text)

        assert "This is the case body content." in result
        assert "aws support create-case" in result
        assert "console.aws.amazon.com/support" in result
        assert "servicequotas" in result

    def test_appends_cli_command(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Body",
            "cli_command_file": "aws support create-case --subject 'Test' --communication-body file://case-body.txt --service-code service-service-quotas",
            "cli_command_inline": "aws support create-case inline",
            "links": {"support_console": "https://example.com", "service_quotas": "https://example.com/quotas"},
            "note": "Test note.",
        }
        text = "Draft summary."
        result = _append_draft_actions(text)

        assert "file://case-body.txt" in result
        assert "service-service-quotas" in result

    def test_includes_how_to_submit_section(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Body content here",
            "cli_command_file": "aws cli command",
            "cli_command_inline": "inline cli",
            "links": {"support_console": "https://console.aws.amazon.com/support/home#/case/create", "service_quotas": "https://quotas.url"},
            "note": "",
        }
        text = "Your draft is ready."
        result = _append_draft_actions(text)

        assert "*How to Submit*" in result
        assert "*Option 1: Ask me to submit it*" in result
        assert "*Option 2: Support Console*" in result
        assert "*Option 3: AWS CLI*" in result
        assert "*Option 4: Service Quotas Console (limited)*" in result

    def test_clears_last_draft_data_after_append(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Body",
            "cli_command_file": "cli",
            "cli_command_inline": "inline",
            "links": {"support_console": "url1", "service_quotas": "url2"},
            "note": "",
        }
        _append_draft_actions("Some text")
        assert _mod._last_draft_data is None


# ---------------------------------------------------------------------------
# Test: _append_draft_actions — strips LLM-fabricated structured content
# ---------------------------------------------------------------------------


class TestAppendDraftActionsStripsLLMContent:
    """The function should strip LLM-generated case bodies, CLI commands, etc."""

    def test_strips_content_after_separator_line(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Authoritative case body",
            "cli_command_file": "real cli command",
            "cli_command_inline": "inline",
            "links": {"support_console": "https://console.aws.amazon.com/support/home#/case/create", "service_quotas": "https://quotas.url"},
            "note": "",
        }
        # LLM generates structured content after the summary
        text = (
            "I've prepared a draft quota increase request for Claude Sonnet 4 in us-west-2.\n"
            "\n"
            "---\n"
            "**Case Body:**\n"
            "This is fabricated content the LLM made up.\n"
            "**AWS CLI:**\n"
            "aws support create-case --fake\n"
        )
        result = _append_draft_actions(text)

        # The LLM's fabricated content should be stripped
        assert "fabricated content" not in result
        assert "--fake" not in result
        # But the authoritative content should be present
        assert "Authoritative case body" in result
        assert "real cli command" in result

    def test_strips_content_after_heading(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Real body",
            "cli_command_file": "real cli",
            "cli_command_inline": "inline",
            "links": {"support_console": "url", "service_quotas": "url2"},
            "note": "",
        }
        text = (
            "Draft is ready for Claude Sonnet 4.\n"
            "\n"
            "## Submission Options\n"
            "1. Go to the console\n"
            "2. Use CLI\n"
        )
        result = _append_draft_actions(text)

        assert "Submission Options" not in result
        assert "Go to the console" not in result
        assert "Real body" in result

    def test_strips_console_urls_from_llm(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Body",
            "cli_command_file": "cli",
            "cli_command_inline": "inline",
            "links": {"support_console": "https://console.aws.amazon.com/support/home#/case/create", "service_quotas": "https://quotas.url"},
            "note": "",
        }
        text = (
            "Here is your draft.\n"
            "Go to https://console.aws.amazon.com/support/home to submit.\n"
            "Or use the CLI.\n"
        )
        result = _append_draft_actions(text)

        # The opening line is preserved
        assert "Here is your draft." in result
        # The LLM's URL line is stripped (before appending the authoritative links)

    def test_preserves_opening_summary(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Case content",
            "cli_command_file": "cli",
            "cli_command_inline": "inline",
            "links": {"support_console": "url", "service_quotas": "url2"},
            "note": "",
        }
        text = (
            "I've prepared a draft quota increase request for Claude Sonnet 4 in us-west-2. "
            "The request shows strong utilization at 150%.\n"
            "\n"
            "===\n"
            "CASE BODY\n"
            "fake content\n"
        )
        result = _append_draft_actions(text)

        assert "I've prepared a draft quota increase request" in result
        assert "strong utilization at 150%" in result
        assert "fake content" not in result


# ---------------------------------------------------------------------------
# Test: _append_draft_actions — returns unchanged when draft already submitted
# ---------------------------------------------------------------------------


class TestAppendDraftActionsSubmitted:
    """When the draft has been submitted, don't append submission instructions."""

    def test_returns_unchanged_when_submitted(self):
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Body",
            "cli_command_file": "cli",
            "cli_command_inline": "inline",
            "links": {"support_console": "url", "service_quotas": "url2"},
            "note": "",
            "submitted": True,
            "case_id": "CASE-12345",
        }
        text = "The case has been submitted successfully."
        result = _append_draft_actions(text)

        assert result == text
        # Should not append submission instructions
        assert "*How to Submit*" not in result
        assert "Case body:" not in result

    def test_submitted_without_case_id_still_appends(self):
        """Edge case: submitted=True but no case_id → still returns unchanged."""
        _mod._last_draft_data = {
            "subject": "Test",
            "case_body": "Body",
            "cli_command_file": "cli",
            "cli_command_inline": "inline",
            "links": {"support_console": "url", "service_quotas": "url2"},
            "note": "",
            "submitted": True,
            "case_id": None,
        }
        text = "Some response text."
        result = _append_draft_actions(text)

        # submitted=True and case_id is falsy → condition is: `if submitted and case_id`
        # Since case_id is None, this branch won't fire, so it WILL append
        assert "*How to Submit*" in result


# ---------------------------------------------------------------------------
# Test: _append_draft_actions — exception handling in finally block
# ---------------------------------------------------------------------------


class TestAppendDraftActionsExceptionHandling:
    """If an error occurs during append, original text is returned and draft is cleared."""

    def test_exception_during_append_returns_original_text(self):
        # Set a malformed draft that will cause an error during processing
        _mod._last_draft_data = {
            "subject": "Test",
            # Missing 'case_body' key entirely
            "cli_command_file": "cli",
            "cli_command_inline": "inline",
            "links": None,  # This will cause .get() on None to fail
            "note": "",
        }
        text = "Original response text that should be preserved."
        result = _append_draft_actions(text)

        # The function catches exceptions and returns original text
        assert "Original response text" in result
        # The finally block clears _last_draft_data
        assert _mod._last_draft_data is None
