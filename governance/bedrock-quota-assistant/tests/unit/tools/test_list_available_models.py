# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for list_available_bedrock_models tool."""

import importlib
from unittest.mock import patch, MagicMock


_mod = importlib.import_module("tools.list_available_bedrock_models")
list_available_bedrock_models = _mod.list_available_bedrock_models


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

REGION = "us-east-1"

SAMPLE_MODELS_RESPONSE = {
    "modelSummaries": [
        {
            "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
            "modelName": "Claude Sonnet 4",
            "providerName": "Anthropic",
            "inputModalities": ["TEXT", "IMAGE"],
            "outputModalities": ["TEXT"],
        },
        {
            "modelId": "anthropic.claude-haiku-4-20250514-v1:0",
            "modelName": "Claude Haiku 4",
            "providerName": "Anthropic",
            "inputModalities": ["TEXT"],
            "outputModalities": ["TEXT"],
        },
        {
            "modelId": "amazon.nova-pro-v1:0",
            "modelName": "Nova Pro",
            "providerName": "Amazon",
            "inputModalities": ["TEXT", "IMAGE"],
            "outputModalities": ["TEXT"],
        },
        {
            "modelId": "meta.llama3-70b-instruct-v1:0",
            "modelName": "Llama 3 70B Instruct",
            "providerName": "Meta",
            "inputModalities": ["TEXT"],
            "outputModalities": ["TEXT"],
        },
    ]
}


# ---------------------------------------------------------------------------
# Test: Returns formatted model list
# ---------------------------------------------------------------------------


class TestReturnsFormattedModelList:
    """Happy path: lists all models with proper formatting."""

    def test_lists_all_models(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert f"Available Bedrock Models in {REGION}:" in result
        assert "=" * 80 in result
        assert "Claude Sonnet 4 (Anthropic)" in result
        assert "Claude Haiku 4 (Anthropic)" in result
        assert "Nova Pro (Amazon)" in result
        assert "Llama 3 70B Instruct (Meta)" in result

    def test_shows_model_id(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert "Model ID: anthropic.claude-sonnet-4-20250514-v1:0" in result
        assert "Model ID: amazon.nova-pro-v1:0" in result

    def test_shows_input_output_modalities(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert "Input: TEXT, IMAGE" in result
        assert "Output: TEXT" in result

    def test_shows_aliases_when_catalog_info_exists(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [SAMPLE_MODELS_RESPONSE["modelSummaries"][0]]
        }

        catalog_info = {
            "name": "Claude Sonnet 4",
            "aliases": ["claude sonnet 4", "sonnet 4", "claude 4 sonnet"],
        }

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=catalog_info):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert "(aliases: claude sonnet 4, sonnet 4)" in result

    def test_no_aliases_when_catalog_info_is_none(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {
            "modelSummaries": [SAMPLE_MODELS_RESPONSE["modelSummaries"][0]]
        }

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert "(aliases:" not in result

    def test_creates_client_with_correct_region(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {"modelSummaries": []}

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            list_available_bedrock_models(region="eu-west-1")

        mock_boto3.client.assert_called_once_with("bedrock", region_name="eu-west-1")


# ---------------------------------------------------------------------------
# Test: Provider filter works (case-insensitive)
# ---------------------------------------------------------------------------


class TestProviderFilter:
    """Provider filter should be case-insensitive and filter correctly."""

    def test_filter_anthropic_only(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION, provider="Anthropic")

        assert "(Filtered by provider: Anthropic)" in result
        assert "Claude Sonnet 4 (Anthropic)" in result
        assert "Claude Haiku 4 (Anthropic)" in result
        assert "Nova Pro (Amazon)" not in result
        assert "Llama 3 70B Instruct (Meta)" not in result

    def test_filter_case_insensitive_lowercase(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION, provider="anthropic")

        assert "Claude Sonnet 4 (Anthropic)" in result
        assert "Nova Pro (Amazon)" not in result

    def test_filter_case_insensitive_uppercase(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION, provider="AMAZON")

        assert "Nova Pro (Amazon)" in result
        assert "Claude Sonnet 4 (Anthropic)" not in result

    def test_filter_partial_match(self):
        """Provider filter uses 'in' matching, so partial names work."""
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION, provider="meta")

        assert "Llama 3 70B Instruct (Meta)" in result
        assert "Claude" not in result

    def test_no_provider_filter_shows_all(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION, provider=None)

        assert "(Filtered by provider:" not in result
        assert "Claude Sonnet 4 (Anthropic)" in result
        assert "Nova Pro (Amazon)" in result
        assert "Llama 3 70B Instruct (Meta)" in result


# ---------------------------------------------------------------------------
# Test: Empty response handled
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    """When no models are returned from the API."""

    def test_empty_model_summaries(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {"modelSummaries": []}

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert f"Available Bedrock Models in {REGION}:" in result
        assert "=" * 80 in result
        # No model entries should appear
        assert "Model ID:" not in result

    def test_missing_model_summaries_key(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = {}

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert f"Available Bedrock Models in {REGION}:" in result
        # Should not crash — get() defaults to []
        assert "Model ID:" not in result

    def test_provider_filter_matches_nothing(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.return_value = SAMPLE_MODELS_RESPONSE

        with patch.object(_mod, "boto3") as mock_boto3, \
             patch.object(_mod, "get_model_info", return_value=None):
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION, provider="NonExistentProvider")

        assert "(Filtered by provider: NonExistentProvider)" in result
        assert "Model ID:" not in result


# ---------------------------------------------------------------------------
# Test: Exception returns error message
# ---------------------------------------------------------------------------


class TestExceptionHandling:
    """API exceptions should be caught and return a helpful error."""

    def test_api_exception_returns_error_message(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.side_effect = Exception("Access denied to bedrock:ListFoundationModels")

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert "Error listing Bedrock models" in result
        assert "Access denied" in result
        assert "bedrock:ListFoundationModels" in result

    def test_client_creation_exception(self):
        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.side_effect = Exception("Unable to locate credentials")
            result = list_available_bedrock_models(region=REGION)

        assert "Error listing Bedrock models" in result
        assert "Unable to locate credentials" in result

    def test_error_message_includes_permission_note(self):
        mock_bedrock = MagicMock()
        mock_bedrock.list_foundation_models.side_effect = RuntimeError("timeout")

        with patch.object(_mod, "boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_bedrock
            result = list_available_bedrock_models(region=REGION)

        assert "proper AWS credentials and permissions" in result
