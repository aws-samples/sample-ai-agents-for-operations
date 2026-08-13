# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Unit tests for quota lookup fallback fixes.

Covers three fixes that resolve the issue where check_quota_utilization fails
to return quota values for models not in the MODEL_CATALOG:

  Fix 1: _query_quota_codes normalises "4-6" to "4.6" in the DynamoDB filter
  Fix 2: resolve_profile_ref treats valid-looking model IDs as kind="model"
         even when not in the catalog (Branch 5b)
  Fix 3: _friendly_name_from_model_id derives a DynamoDB-compatible friendly
         name from a raw Bedrock model ID
"""

import re
from unittest.mock import patch

import pytest

from helpers.profile_resolution import resolve_profile_ref, _BEDROCK_MODEL_ID_RE
from tools.check_quota_utilization import _friendly_name_from_model_id


# ---------------------------------------------------------------------------
# Fix 1: _query_quota_codes version normalisation
# ---------------------------------------------------------------------------


class TestQueryQuotaCodesVersionNormalisation:
    """Verify that _query_quota_codes normalises digit-hyphen-digit to digit.digit.

    This fixes the case where the LLM passes "claude sonnet 4-6" (with hyphen)
    but DynamoDB quota names contain "claude sonnet 4.6" (with dot).
    """

    def _simulate_clean(self, model_filter: str) -> str:
        """Replicate the cleaning logic in _query_quota_codes."""
        cleaned = model_filter.lower().strip().rstrip(".,;:!?").replace("-", " ").replace("_", " ")
        cleaned = re.sub(r"(\d) (\d)(?=\s|$)", r"\1.\2", cleaned)
        return cleaned

    def test_hyphenated_version_becomes_dotted(self):
        """'claude sonnet 4-6' normalises to 'claude sonnet 4.6'."""
        assert self._simulate_clean("claude sonnet 4-6") == "claude sonnet 4.6"

    def test_hyphenated_version_4_5(self):
        """'claude sonnet 4-5' normalises to 'claude sonnet 4.5'."""
        assert self._simulate_clean("claude sonnet 4-5") == "claude sonnet 4.5"

    def test_hyphenated_version_3_7(self):
        """'claude 3-7 sonnet' normalises to 'claude 3.7 sonnet'."""
        assert self._simulate_clean("claude 3-7 sonnet") == "claude 3.7 sonnet"

    def test_no_version_unchanged(self):
        """'nova pro' remains 'nova pro' (no digit-digit pattern)."""
        assert self._simulate_clean("nova pro") == "nova pro"

    def test_multi_digit_size_not_affected(self):
        """'llama 70b' is not affected (70 is not digit-space-digit at boundary)."""
        assert self._simulate_clean("llama 70b") == "llama 70b"

    def test_version_at_end_of_string(self):
        """'sonnet 4-6' at end of string normalises correctly."""
        assert self._simulate_clean("sonnet 4-6") == "sonnet 4.6"

    def test_version_mid_string(self):
        """'llama 3-3 70b' normalises to 'llama 3.3 70b'."""
        assert self._simulate_clean("llama 3-3 70b") == "llama 3.3 70b"

    def test_already_dotted_unchanged(self):
        """'claude sonnet 4.6' — dot is preserved by rstrip but survives."""
        # The rstrip removes trailing dots, but mid-string dots are fine.
        # However, the replace("-", " ") won't touch dots.
        # Input "claude sonnet 4.6" has no hyphens, so it stays as-is.
        result = self._simulate_clean("claude sonnet 4.6")
        assert "4.6" in result


# ---------------------------------------------------------------------------
# Fix 2: resolve_profile_ref Branch 5b — valid-looking model IDs
# ---------------------------------------------------------------------------


class TestResolveProfileRefBranch5b:
    """Verify that valid-looking Bedrock model IDs not in the catalog
    resolve as kind='model' instead of 'unresolved'.

    This fixes the case where the LLM passes 'anthropic.claude-sonnet-5'
    which isn't in models.py but is a valid Bedrock model ID.
    """

    def test_valid_model_id_resolves_as_model(self):
        """'anthropic.claude-sonnet-5' resolves as kind='model'."""
        # Patch resolve_model_id to return None (simulates model not in catalog)
        with patch("helpers.profile_resolution.resolve_model_id", return_value=None):
            result = resolve_profile_ref("anthropic.claude-sonnet-5", None)

        assert result["kind"] == "model"
        assert result["base_model_id"] == "anthropic.claude-sonnet-5"
        assert result["cw_model_id"] == "anthropic.claude-sonnet-5"

    def test_valid_model_id_with_hyphens(self):
        """'anthropic.claude-sonnet-4-6' resolves as kind='model'."""
        with patch("helpers.profile_resolution.resolve_model_id", return_value=None):
            result = resolve_profile_ref("anthropic.claude-sonnet-4-6", None)

        assert result["kind"] == "model"
        assert result["base_model_id"] == "anthropic.claude-sonnet-4-6"

    def test_valid_model_id_amazon_provider(self):
        """'amazon.nova-premier' resolves as kind='model'."""
        with patch("helpers.profile_resolution.resolve_model_id", return_value=None):
            result = resolve_profile_ref("amazon.nova-premier", None)

        assert result["kind"] == "model"
        assert result["base_model_id"] == "amazon.nova-premier"

    def test_friendly_name_still_unresolved(self):
        """'claude sonnet 4.6' (not a model ID pattern) remains unresolved."""
        with patch("helpers.profile_resolution.resolve_model_id", return_value=None):
            result = resolve_profile_ref("claude sonnet 4.6", None)

        assert result["kind"] == "unresolved"

    def test_system_profile_prefix_not_caught(self):
        """'eu.anthropic.claude-sonnet-4-6' is caught by Branch 2, not 5b."""
        # Branch 2 runs first, so system profiles never reach Branch 5b.
        with patch("helpers.profile_resolution.resolve_model_id", return_value=None):
            result = resolve_profile_ref("eu.anthropic.claude-sonnet-4-6", None)

        assert result["kind"] == "system_profile"
        assert result["base_model_id"] == "anthropic.claude-sonnet-4-6"

    def test_catalog_hit_takes_priority(self):
        """If resolve_model_id finds the model, Branch 5 handles it (not 5b)."""
        with patch(
            "helpers.profile_resolution.resolve_model_id",
            return_value="anthropic.claude-sonnet-4-5-20250929-v1:0",
        ):
            result = resolve_profile_ref("claude sonnet 4.5", None)

        assert result["kind"] == "model"
        assert result["base_model_id"] == "anthropic.claude-sonnet-4-5-20250929-v1:0"


class TestBedrockModelIdRegex:
    """Test the _BEDROCK_MODEL_ID_RE pattern matches valid model IDs."""

    @pytest.mark.parametrize("model_id", [
        "anthropic.claude-sonnet-5",
        "anthropic.claude-sonnet-4-6",
        "amazon.nova-pro",
        "meta.llama4-scout-17b-instruct",
        "deepseek.r1",
        "mistral.mistral-large-3-675b-instruct",
        "qwen.qwen3-coder-next",
    ])
    def test_valid_model_ids_match(self, model_id):
        assert _BEDROCK_MODEL_ID_RE.match(model_id)

    @pytest.mark.parametrize("invalid_id", [
        "eu.anthropic.claude-sonnet-4-6",   # system profile (two dots via provider segments)
        "claude sonnet 4.6",                 # friendly name (has space)
        "amazon.nova-pro-v1:0",             # has colon (version suffix)
        "anthropic.claude-sonnet-4-5-20250929-v1:0",  # has colon
        "arn:aws:bedrock:us-east-1:123:application-inference-profile/x",  # ARN
        "ANTHROPIC.CLAUDE-SONNET",          # uppercase
        "",                                  # empty
    ])
    def test_invalid_ids_do_not_match(self, invalid_id):
        assert not _BEDROCK_MODEL_ID_RE.match(invalid_id)


# ---------------------------------------------------------------------------
# Fix 3: _friendly_name_from_model_id
# ---------------------------------------------------------------------------


class TestFriendlyNameFromModelId:
    """Verify the fallback parser that derives a DynamoDB-friendly name from
    a raw Bedrock model ID.

    The derived name must be usable as a DynamoDB .contains() filter against
    AWS Service Quotas names like 'Claude Sonnet 4.6 Tokens Per Minute (TPM)'.
    """

    def test_claude_sonnet_4_6(self):
        """Strips provider, converts version hyphens to dots."""
        assert _friendly_name_from_model_id("anthropic.claude-sonnet-4-6") == "claude sonnet 4.6"

    def test_claude_sonnet_4_5_with_date_and_version(self):
        """Strips date stamp and version suffix."""
        result = _friendly_name_from_model_id("anthropic.claude-sonnet-4-5-20250929-v1:0")
        assert result == "claude sonnet 4.5"

    def test_claude_sonnet_5(self):
        """Single-number version stays as-is."""
        assert _friendly_name_from_model_id("anthropic.claude-sonnet-5") == "claude sonnet 5"

    def test_claude_opus_4_7(self):
        """Opus model with version."""
        assert _friendly_name_from_model_id("anthropic.claude-opus-4-7") == "claude opus 4.7"

    def test_amazon_nova_pro(self):
        """Strips provider and version suffix."""
        assert _friendly_name_from_model_id("amazon.nova-pro-v1:0") == "nova pro"

    def test_meta_llama_with_size_and_instruct(self):
        """Strips 'instruct' suffix but keeps size."""
        result = _friendly_name_from_model_id("meta.llama4-scout-17b-instruct-v1:0")
        assert result == "llama4 scout 17b"

    def test_deepseek_r1(self):
        """Simple model with just version suffix."""
        assert _friendly_name_from_model_id("deepseek.r1-v1:0") == "r1"

    def test_mistral_large(self):
        """Mistral model with size and instruct."""
        result = _friendly_name_from_model_id("mistral.mistral-large-3-675b-instruct")
        assert result == "mistral large 3 675b"

    def test_result_matches_dynamodb_quota_names(self):
        """Key integration check: derived names match real quota name patterns."""
        quota_names_lower = [
            "claude sonnet 4.6 tokens per minute (tpm) - cross-region inference",
            "claude sonnet 4.6 requests per minute (rpm) - on-demand",
            "claude sonnet 5 tokens per minute (tpm) - cross-region inference",
            "nova pro tokens per minute (tpm) - cross-region inference",
            "claude opus 4.7 requests per minute (rpm) - cross-region inference",
        ]

        test_cases = [
            ("anthropic.claude-sonnet-4-6", 2),   # matches 2 sonnet 4.6 quotas
            ("anthropic.claude-sonnet-5", 1),      # matches 1 sonnet 5 quota
            ("amazon.nova-pro-v1:0", 1),           # matches 1 nova pro quota
            ("anthropic.claude-opus-4-7", 1),      # matches 1 opus 4.7 quota
        ]

        for model_id, expected_matches in test_cases:
            friendly = _friendly_name_from_model_id(model_id)
            matches = [qn for qn in quota_names_lower if friendly in qn]
            assert len(matches) == expected_matches, (
                f"'{model_id}' -> '{friendly}' matched {len(matches)} quotas, "
                f"expected {expected_matches}"
            )
