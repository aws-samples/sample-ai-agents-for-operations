# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for get_bedrock_model_quotas tool."""

import importlib
from unittest.mock import patch


_mod = importlib.import_module("tools.get_bedrock_model_quotas")
get_bedrock_model_quotas = _mod.get_bedrock_model_quotas


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

REGION = "us-east-1"

SAMPLE_QUOTA_CODES = [
    {"PK": "quota", "SK": "L-ABCD1234", "quota_code": "L-ABCD1234", "quota_name": "Claude Sonnet 4 Tokens Per Minute", "quota_name_lower": "claude sonnet 4 tokens per minute"},
    {"PK": "quota", "SK": "L-EFGH5678", "quota_code": "L-EFGH5678", "quota_name": "Claude Sonnet 4 Requests Per Minute", "quota_name_lower": "claude sonnet 4 requests per minute"},
]

SAMPLE_LIVE_VALUES = [
    {"name": "Claude Sonnet 4 Tokens Per Minute", "value": 100000, "unit": "None", "adjustable": True},
    {"name": "Claude Sonnet 4 Requests Per Minute", "value": 500, "unit": "None", "adjustable": True},
]


# ---------------------------------------------------------------------------
# Test: Happy path — returns formatted quota info
# ---------------------------------------------------------------------------


class TestHappyPath:
    """When DynamoDB returns quota codes and live values are fetched successfully."""

    def test_returns_formatted_output_with_quotas(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_LIVE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_LIVE_VALUES):
            result = get_bedrock_model_quotas(region=REGION, model_filter="claude sonnet 4")

        assert f"Bedrock Quotas in {REGION}:" in result
        assert "(Filtered by: claude sonnet 4)" in result
        assert "Found 2 quotas" in result
        assert "Claude Sonnet 4 Tokens Per Minute" in result
        assert "Current Value: 100000" in result
        assert "Adjustable: Yes" in result

    def test_no_filter_returns_all_quotas(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_LIVE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=SAMPLE_LIVE_VALUES):
            result = get_bedrock_model_quotas(region=REGION, model_filter=None)

        assert f"Bedrock Quotas in {REGION}:" in result
        assert "(Filtered by:" not in result
        assert "Found 2 quotas" in result

    def test_filter_strict_model_match_is_skipped_when_no_filter(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_LIVE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match") as mock_filter:
            get_bedrock_model_quotas(region=REGION, model_filter=None)

        # When no model_filter, _filter_strict_model_match should not be called
        mock_filter.assert_not_called()

    def test_throttled_quota_renders_rate_limited_message(self):
        throttled_values = [
            {"name": "Claude Sonnet 4 Tokens Per Minute", "value": "THROTTLED", "unit": "", "adjustable": False},
        ]
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES[:1]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=throttled_values), \
             patch.object(_mod, "_filter_strict_model_match", return_value=throttled_values):
            result = get_bedrock_model_quotas(region=REGION, model_filter="claude sonnet 4")

        assert "Unable to fetch (rate limited)" in result

    def test_unit_displayed_when_present_and_not_none(self):
        values_with_unit = [
            {"name": "Claude Sonnet 4 RPM", "value": 500, "unit": "Count/Second", "adjustable": True},
        ]
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES[:1]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=values_with_unit), \
             patch.object(_mod, "_filter_strict_model_match", return_value=values_with_unit):
            result = get_bedrock_model_quotas(region=REGION, model_filter="claude sonnet 4")

        assert "500 Count/Second" in result

    def test_non_adjustable_quota_shows_no(self):
        non_adjustable = [
            {"name": "Some Quota", "value": 10, "unit": "None", "adjustable": False},
        ]
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES[:1]), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=non_adjustable), \
             patch.object(_mod, "_filter_strict_model_match", return_value=non_adjustable):
            result = get_bedrock_model_quotas(region=REGION, model_filter="some")

        assert "Adjustable: No" in result


# ---------------------------------------------------------------------------
# Test: Too many results guard (>50 quota codes)
# ---------------------------------------------------------------------------


class TestTooManyResultsGuard:
    """When DynamoDB returns more than 50 quota codes, the tool should advise narrowing."""

    def test_over_50_codes_returns_guard_message(self):
        # Create 51 dummy quota codes
        many_codes = [
            {"PK": "quota", "SK": f"L-{i:08d}", "quota_code": f"L-{i:08d}", "quota_name": f"Quota {i}", "quota_name_lower": f"quota {i}"}
            for i in range(51)
        ]
        with patch.object(_mod, "_query_quota_codes", return_value=many_codes):
            result = get_bedrock_model_quotas(region=REGION, model_filter="broad")

        assert "Found 51 matching quotas" in result
        assert "too many to fetch live values" in result
        assert "narrow your search" in result

    def test_exactly_50_codes_does_not_trigger_guard(self):
        codes_50 = [
            {"PK": "quota", "SK": f"L-{i:08d}", "quota_code": f"L-{i:08d}", "quota_name": f"Quota {i}", "quota_name_lower": f"quota {i}"}
            for i in range(50)
        ]
        values_50 = [
            {"name": f"Quota {i}", "value": 100, "unit": "None", "adjustable": True}
            for i in range(50)
        ]
        with patch.object(_mod, "_query_quota_codes", return_value=codes_50), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=values_50), \
             patch.object(_mod, "_filter_strict_model_match", return_value=values_50):
            result = get_bedrock_model_quotas(region=REGION, model_filter="quota")

        assert "too many" not in result
        assert "Found 50 quotas" in result


# ---------------------------------------------------------------------------
# Test: No results after filtering
# ---------------------------------------------------------------------------


class TestNoResults:
    """When quota codes exist but live values return empty or filtering removes all."""

    def test_empty_live_values_returns_no_quotas_message(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = get_bedrock_model_quotas(region=REGION, model_filter="nonexistent")

        assert "No Bedrock quotas found" in result
        assert "nonexistent" in result
        assert "may not be available in this region" in result

    def test_strict_filter_removes_all_results(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_LIVE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", return_value=[]):
            result = get_bedrock_model_quotas(region=REGION, model_filter="sonnet 4")

        assert "No Bedrock quotas found" in result

    def test_no_filter_empty_results(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=[]):
            result = get_bedrock_model_quotas(region=REGION, model_filter=None)

        assert "No Bedrock quotas found" in result
        assert REGION in result


# ---------------------------------------------------------------------------
# Test: DynamoDB empty → fallback to pagination
# ---------------------------------------------------------------------------


class TestDynamoDBEmptyFallback:
    """When _query_quota_codes returns empty, fall back to _fallback_paginate_quotas."""

    def test_empty_quota_codes_triggers_fallback(self):
        fallback_result = "Bedrock Quotas in us-east-1 (direct API - cache unavailable):\nFound 5 quotas"
        with patch.object(_mod, "_query_quota_codes", return_value=[]), \
             patch.object(_mod, "_fallback_paginate_quotas", return_value=fallback_result) as mock_fallback:
            result = get_bedrock_model_quotas(region=REGION, model_filter="claude")

        mock_fallback.assert_called_once_with(REGION, "claude")
        assert result == fallback_result

    def test_none_quota_codes_triggers_fallback(self):
        fallback_result = "Bedrock Quotas in us-west-2 (direct API - cache unavailable):\nFound 0 quotas"
        with patch.object(_mod, "_query_quota_codes", return_value=None), \
             patch.object(_mod, "_fallback_paginate_quotas", return_value=fallback_result) as mock_fallback:
            result = get_bedrock_model_quotas(region="us-west-2", model_filter=None)

        mock_fallback.assert_called_once_with("us-west-2", None)
        assert result == fallback_result


# ---------------------------------------------------------------------------
# Test: Exception handling
# ---------------------------------------------------------------------------


class TestExceptionHandling:
    """When an unexpected exception occurs, the tool returns an error message."""

    def test_query_quota_codes_raises_exception(self):
        with patch.object(_mod, "_query_quota_codes", side_effect=RuntimeError("DynamoDB connection failed")):
            result = get_bedrock_model_quotas(region=REGION, model_filter="claude")

        assert "Error retrieving Bedrock quotas" in result
        assert "DynamoDB connection failed" in result
        assert "proper permissions" in result

    def test_fetch_live_values_raises_exception(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", side_effect=Exception("Service Quotas unavailable")):
            result = get_bedrock_model_quotas(region=REGION, model_filter="claude")

        assert "Error retrieving Bedrock quotas" in result
        assert "Service Quotas unavailable" in result

    def test_filter_strict_raises_exception(self):
        with patch.object(_mod, "_query_quota_codes", return_value=SAMPLE_QUOTA_CODES), \
             patch.object(_mod, "_fetch_live_quota_values", return_value=SAMPLE_LIVE_VALUES), \
             patch.object(_mod, "_filter_strict_model_match", side_effect=ValueError("Regex error")):
            result = get_bedrock_model_quotas(region=REGION, model_filter="(invalid")

        assert "Error retrieving Bedrock quotas" in result
        assert "Regex error" in result
