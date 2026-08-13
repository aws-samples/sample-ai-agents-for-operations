# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Unit tests for ``resolve_model_id`` and ``get_model_info`` from ``src/models.py``.

These are pure lookup functions against the MODEL_CATALOG dict and the
FRIENDLY_NAME_MAP reverse index built at import time.
"""

import pytest

from models import resolve_model_id, get_model_info, MODEL_CATALOG


# ---------------------------------------------------------------------------
# Tests: resolve_model_id — Known aliases
# ---------------------------------------------------------------------------


class TestResolveModelIdKnownAliases:
    """Known aliases resolve to their correct model IDs."""

    @pytest.mark.parametrize(
        "alias,expected_id",
        [
            ("claude sonnet 4.5", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
            ("sonnet 4.5", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
            ("claude 4.5 sonnet", "anthropic.claude-sonnet-4-5-20250929-v1:0"),
            ("claude sonnet 4", "anthropic.claude-sonnet-4-20250514-v1:0"),
            ("sonnet 4", "anthropic.claude-sonnet-4-20250514-v1:0"),
            ("claude opus 4", "anthropic.claude-opus-4-20250514-v1:0"),
            ("opus 4", "anthropic.claude-opus-4-20250514-v1:0"),
            ("claude opus 4.5", "anthropic.claude-opus-4-5-20251101-v1:0"),
            ("claude haiku 4.5", "anthropic.claude-haiku-4-5-20251001-v1:0"),
            ("nova pro", "amazon.nova-pro-v1:0"),
            ("nova lite", "amazon.nova-lite-v1:0"),
            ("nova micro", "amazon.nova-micro-v1:0"),
            ("nova premier", "amazon.nova-premier-v1:0"),
            ("llama 4 maverick", "meta.llama4-maverick-17b-instruct-v1:0"),
            ("llama 4 scout", "meta.llama4-scout-17b-instruct-v1:0"),
            ("deepseek r1", "deepseek.r1-v1:0"),
            ("mistral large", "mistral.mistral-large-3-675b-instruct"),
            ("command r+", "cohere.command-r-plus-v1:0"),
            ("titan embeddings", "amazon.titan-embed-text-v2:0"),
        ],
    )
    def test_known_alias_resolves(self, alias, expected_id):
        """Each known alias in FRIENDLY_NAME_MAP resolves to the correct model ID."""
        assert resolve_model_id(alias) == expected_id

    def test_official_name_resolves(self):
        """The official 'name' field from MODEL_CATALOG also resolves."""
        assert resolve_model_id("Claude Sonnet 4.5") == "anthropic.claude-sonnet-4-5-20250929-v1:0"
        assert resolve_model_id("Nova Pro") == "amazon.nova-pro-v1:0"
        assert resolve_model_id("DeepSeek R1") == "deepseek.r1-v1:0"

    def test_full_model_id_resolves_to_itself(self):
        """Passing the full model ID should resolve to itself."""
        model_id = "anthropic.claude-sonnet-4-20250514-v1:0"
        assert resolve_model_id(model_id) == model_id


# ---------------------------------------------------------------------------
# Tests: resolve_model_id — Case insensitivity
# ---------------------------------------------------------------------------


class TestResolveModelIdCaseInsensitive:
    """Resolution should be case-insensitive."""

    @pytest.mark.parametrize(
        "input_str",
        [
            "Claude Sonnet 4",
            "CLAUDE SONNET 4",
            "claude SONNET 4",
            "Claude sonnet 4",
            "cLaUdE sOnNeT 4",
        ],
    )
    def test_case_insensitive_resolution(self, input_str):
        """Mixed-case inputs resolve correctly."""
        assert resolve_model_id(input_str) == "anthropic.claude-sonnet-4-20250514-v1:0"

    def test_case_insensitive_with_whitespace(self):
        """Leading/trailing whitespace is stripped."""
        assert resolve_model_id("  claude sonnet 4  ") == "anthropic.claude-sonnet-4-20250514-v1:0"


# ---------------------------------------------------------------------------
# Tests: resolve_model_id — Partial matches
# ---------------------------------------------------------------------------


class TestResolveModelIdPartialMatch:
    """Partial matches work via substring matching."""

    def test_partial_match_sonnet(self):
        """A substring that's contained in a name resolves via fuzzy match."""
        # "sonnet" is contained in "claude sonnet 4.5", "claude 3.7 sonnet", etc.
        result = resolve_model_id("sonnet")
        assert result is not None
        assert "sonnet" in result.lower() or "sonnet" in MODEL_CATALOG[result]["name"].lower()

    def test_partial_match_nova(self):
        """'nova' should match one of the Nova models."""
        result = resolve_model_id("nova")
        assert result is not None
        assert "nova" in result.lower()

    def test_partial_match_llama(self):
        """'llama' should match one of the Llama models."""
        result = resolve_model_id("llama")
        assert result is not None
        assert "llama" in result.lower()


# ---------------------------------------------------------------------------
# Tests: resolve_model_id — Unknown strings
# ---------------------------------------------------------------------------


class TestResolveModelIdUnknown:
    """Unknown model names return None."""

    @pytest.mark.parametrize(
        "unknown_input",
        [
            "gpt-4",
            "gemini pro",
            "totally-made-up-model",
            "xyz123",
            "chatgpt",
        ],
    )
    def test_unknown_model_returns_none(self, unknown_input):
        """Unrecognized model names return None."""
        assert resolve_model_id(unknown_input) is None


# ---------------------------------------------------------------------------
# Tests: get_model_info
# ---------------------------------------------------------------------------


class TestGetModelInfo:
    """Tests for get_model_info which returns full catalog entries."""

    def test_valid_model_id_returns_entry(self):
        """A valid model_id returns its full catalog entry."""
        info = get_model_info("anthropic.claude-sonnet-4-20250514-v1:0")
        assert info is not None
        assert info["name"] == "Claude Sonnet 4"
        assert info["provider"] == "Anthropic"
        assert "TEXT" in info["input"]
        assert "IMAGE" in info["input"]
        assert "TEXT" in info["output"]
        assert "aliases" in info
        assert "claude sonnet 4" in info["aliases"]

    def test_another_valid_model_id(self):
        """Verify another model entry."""
        info = get_model_info("amazon.nova-pro-v1:0")
        assert info is not None
        assert info["name"] == "Nova Pro"
        assert info["provider"] == "Amazon"
        assert "VIDEO" in info["input"]

    def test_embedding_model_info(self):
        """Embedding model returns EMBEDDING output type."""
        info = get_model_info("amazon.titan-embed-text-v2:0")
        assert info is not None
        assert "EMBEDDING" in info["output"]
        assert info["provider"] == "Amazon"

    def test_unknown_model_id_returns_none(self):
        """An unknown model_id returns None."""
        assert get_model_info("nonexistent.model-v1:0") is None

    def test_empty_string_returns_none(self):
        """Empty string returns None."""
        assert get_model_info("") is None

    def test_all_catalog_entries_have_required_fields(self):
        """Every entry in MODEL_CATALOG has name, provider, input, output, aliases."""
        for model_id, info in MODEL_CATALOG.items():
            assert "name" in info, f"{model_id} missing 'name'"
            assert "provider" in info, f"{model_id} missing 'provider'"
            assert "input" in info, f"{model_id} missing 'input'"
            assert "output" in info, f"{model_id} missing 'output'"
            assert "aliases" in info, f"{model_id} missing 'aliases'"
            assert len(info["aliases"]) > 0, f"{model_id} has empty aliases"


# ---------------------------------------------------------------------------
# Tests: FRIENDLY_NAME_MAP consistency
# ---------------------------------------------------------------------------


class TestFriendlyNameMapConsistency:
    """The FRIENDLY_NAME_MAP should be consistent with MODEL_CATALOG."""

    def test_all_aliases_in_map(self):
        """Every alias from every model should be in FRIENDLY_NAME_MAP."""
        from models import FRIENDLY_NAME_MAP

        for model_id, info in MODEL_CATALOG.items():
            for alias in info["aliases"]:
                assert alias.lower() in FRIENDLY_NAME_MAP, (
                    f"alias '{alias}' for {model_id} not in FRIENDLY_NAME_MAP"
                )
                assert FRIENDLY_NAME_MAP[alias.lower()] == model_id

    def test_all_official_names_in_map(self):
        """Every official model name should be in FRIENDLY_NAME_MAP."""
        from models import FRIENDLY_NAME_MAP

        for model_id, info in MODEL_CATALOG.items():
            name_lower = info["name"].lower()
            assert name_lower in FRIENDLY_NAME_MAP, (
                f"official name '{info['name']}' for {model_id} not in FRIENDLY_NAME_MAP"
            )
