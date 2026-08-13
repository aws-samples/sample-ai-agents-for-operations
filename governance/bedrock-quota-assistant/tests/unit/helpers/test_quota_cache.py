# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Unit tests for ``src/helpers/quota_cache.py``.

Covers:
- ``_filter_strict_model_match`` — pure function, tested directly
- ``_query_quota_codes`` — requires DynamoDB Table.query mock
- ``_fetch_live_quota_values`` — requires ServiceQuotas client mock
- ``_fallback_paginate_quotas`` — requires ServiceQuotas paginator mock
"""

import importlib
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# Import the module so we can patch boto3 at the module level
_mod = importlib.import_module("helpers.quota_cache")

# Import the pure function directly
from helpers.quota_cache import _filter_strict_model_match


# ---------------------------------------------------------------------------
# Tests: _filter_strict_model_match (pure function)
# ---------------------------------------------------------------------------


class TestFilterStrictModelMatch:
    """Test the regex-based filter that excludes sub-version matches."""

    def test_exact_match_passes(self):
        """A quota whose name matches exactly passes the filter."""
        quotas = [{"name": "Claude Sonnet 4 TPM"}]
        result = _filter_strict_model_match(quotas, "Claude Sonnet 4")
        assert len(result) == 1
        assert result[0]["name"] == "Claude Sonnet 4 TPM"

    def test_sub_version_excluded(self):
        """A quota for 'Claude Sonnet 4.5' is excluded when filtering for 'Claude Sonnet 4'."""
        quotas = [
            {"name": "Claude Sonnet 4 TPM"},
            {"name": "Claude Sonnet 4.5 TPM"},
        ]
        result = _filter_strict_model_match(quotas, "Claude Sonnet 4")
        assert len(result) == 1
        assert result[0]["name"] == "Claude Sonnet 4 TPM"

    def test_case_insensitive(self):
        """Filter is case-insensitive."""
        quotas = [{"name": "CLAUDE SONNET 4 RPM"}]
        result = _filter_strict_model_match(quotas, "claude sonnet 4")
        assert len(result) == 1

    def test_no_matches(self):
        """Returns empty list when nothing matches."""
        quotas = [
            {"name": "Nova Pro TPM"},
            {"name": "Nova Lite RPM"},
        ]
        result = _filter_strict_model_match(quotas, "Claude Sonnet 4")
        assert result == []

    def test_multiple_matches(self):
        """Multiple quotas for the same model all pass."""
        quotas = [
            {"name": "Claude Opus 4 TPM limit"},
            {"name": "Claude Opus 4 RPM limit"},
            {"name": "Claude Opus 4 invocations"},
            {"name": "Claude Opus 4.5 TPM limit"},
        ]
        result = _filter_strict_model_match(quotas, "Claude Opus 4")
        assert len(result) == 3

    def test_model_name_with_special_regex_chars(self):
        """Model names with regex-special chars (like '.') are escaped properly."""
        quotas = [
            {"name": "Llama 3.1 70B TPM"},
            {"name": "Llama 3.1 70B RPM"},
            {"name": "Llama 3.10 70B TPM"},  # sub-version match (3.1 followed by digit 0)
        ]
        result = _filter_strict_model_match(quotas, "Llama 3.1 70B")
        # The regex is re.escape("llama 3.1 70b") + r"(?!\.\d)" which becomes
        # "llama 3\.1 70b(?!\.\d)". "Llama 3.10 70B" does NOT contain the
        # substring "3.1 70b" (it has "3.10 70b"), so it's correctly excluded.
        assert len(result) == 2

    def test_empty_quotas_list(self):
        """Empty input returns empty output."""
        assert _filter_strict_model_match([], "Claude Sonnet 4") == []

    def test_version_boundary_with_dot_digit(self):
        """The negative lookahead specifically blocks '.<digit>' after the name."""
        quotas = [
            {"name": "Nova 2 Lite TPM"},
            {"name": "Nova 2 Lite.5 TPM"},  # hypothetical sub-version
        ]
        # For "Nova 2 Lite" — lookahead checks char after "nova 2 lite"
        # In "nova 2 lite.5 tpm" next is ".5" which matches (?!\.\d) — excluded
        result = _filter_strict_model_match(quotas, "Nova 2 Lite")
        assert len(result) == 1
        assert result[0]["name"] == "Nova 2 Lite TPM"


# ---------------------------------------------------------------------------
# Tests: _query_quota_codes (DynamoDB mock)
# ---------------------------------------------------------------------------


class TestQueryQuotaCodes:
    """Tests for _query_quota_codes using mocked DynamoDB."""

    def _make_mock_table(self, pages):
        """Create a mock DynamoDB table that returns paginated results.

        Args:
            pages: list of (items, has_more) tuples. Each page returns the items
                   and optionally includes a LastEvaluatedKey if has_more is True.
        """
        mock_table = MagicMock()
        responses = []
        for items, has_more in pages:
            resp = {"Items": items}
            if has_more:
                resp["LastEvaluatedKey"] = {"PK": "quota", "SK": "next-key"}
            responses.append(resp)
        mock_table.query.side_effect = responses
        return mock_table

    def _patch_dynamodb(self, mock_table):
        """Return a patch context for boto3.resource that returns our mock table."""
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table
        return patch.object(_mod, "boto3", **{"resource.return_value": mock_resource})

    def test_single_page_no_filter(self):
        """Single page of results without model filter."""
        items = [
            {"PK": "quota", "SK": "Q001", "quota_code": "L-ABC123", "quota_name": "Claude Sonnet 4 TPM"},
            {"PK": "quota", "SK": "Q002", "quota_code": "L-DEF456", "quota_name": "Nova Pro RPM"},
        ]
        mock_table = self._make_mock_table([(items, False)])

        with self._patch_dynamodb(mock_table):
            result = _mod._query_quota_codes()

        assert len(result) == 2
        assert result[0]["quota_code"] == "L-ABC123"
        assert result[1]["quota_code"] == "L-DEF456"

    def test_paginated_results(self):
        """Multiple pages are collected together."""
        page1_items = [
            {"PK": "quota", "SK": "Q001", "quota_code": "L-AAA"},
        ]
        page2_items = [
            {"PK": "quota", "SK": "Q002", "quota_code": "L-BBB"},
        ]
        mock_table = self._make_mock_table([
            (page1_items, True),
            (page2_items, False),
        ])

        with self._patch_dynamodb(mock_table):
            result = _mod._query_quota_codes()

        assert len(result) == 2
        assert mock_table.query.call_count == 2

    def test_metadata_items_excluded(self):
        """Items with SK='metadata' are filtered out."""
        items = [
            {"PK": "quota", "SK": "metadata", "last_updated": "2026-06-19"},
            {"PK": "quota", "SK": "Q001", "quota_code": "L-XYZ"},
        ]
        mock_table = self._make_mock_table([(items, False)])

        with self._patch_dynamodb(mock_table):
            result = _mod._query_quota_codes()

        assert len(result) == 1
        assert result[0]["quota_code"] == "L-XYZ"

    def test_model_filter_applied(self):
        """When model_filter is provided, a FilterExpression is added to the query."""
        items = [
            {"PK": "quota", "SK": "Q001", "quota_code": "L-111", "quota_name_lower": "claude sonnet 4 tpm"},
        ]
        mock_table = self._make_mock_table([(items, False)])

        with self._patch_dynamodb(mock_table):
            result = _mod._query_quota_codes(model_filter="Claude Sonnet 4")

        assert len(result) == 1
        # Verify FilterExpression was passed
        query_kwargs = mock_table.query.call_args[1]
        assert "FilterExpression" in query_kwargs

    def test_model_filter_cleans_input(self):
        """model_filter with trailing punctuation and special chars is cleaned."""
        items = []
        mock_table = self._make_mock_table([(items, False)])

        with self._patch_dynamodb(mock_table):
            _mod._query_quota_codes(model_filter="Claude-Sonnet_4.!")

        query_kwargs = mock_table.query.call_args[1]
        # The cleaned filter should be "claude sonnet 4" (lowered, stripped, punctuation removed)
        assert "FilterExpression" in query_kwargs

    def test_empty_model_filter_no_filter_expression(self):
        """An empty string model_filter doesn't add a FilterExpression."""
        items = []
        mock_table = self._make_mock_table([(items, False)])

        with self._patch_dynamodb(mock_table):
            _mod._query_quota_codes(model_filter="")

        query_kwargs = mock_table.query.call_args[1]
        assert "FilterExpression" not in query_kwargs

    def test_exception_returns_empty_list(self):
        """DynamoDB errors are caught and return an empty list."""
        mock_table = MagicMock()
        mock_table.query.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "Query",
        )
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch.object(_mod, "boto3", **{"resource.return_value": mock_resource}):
            result = _mod._query_quota_codes()

        assert result == []


# ---------------------------------------------------------------------------
# Tests: _fetch_live_quota_values (ServiceQuotas client mock)
# ---------------------------------------------------------------------------


class TestFetchLiveQuotaValues:
    """Tests for _fetch_live_quota_values with mocked ServiceQuotas client."""

    def _make_mock_sq_client(self, responses=None, side_effects=None):
        """Create a mock ServiceQuotas client.

        Args:
            responses: list of dicts to return from get_service_quota (one per call)
            side_effects: list of exceptions or responses (overrides responses if set)
        """
        mock_client = MagicMock()
        if side_effects is not None:
            mock_client.get_service_quota.side_effect = side_effects
        elif responses is not None:
            mock_client.get_service_quota.side_effect = responses
        return mock_client

    def _patch_boto3_client(self, mock_client):
        """Return a patch context for boto3.client that returns our mock."""
        return patch.object(_mod, "boto3", **{"client.return_value": mock_client})

    def test_successful_fetch(self):
        """Successfully fetches quota values for multiple codes."""
        mock_client = self._make_mock_sq_client(responses=[
            {"Quota": {"QuotaName": "Claude Sonnet 4 TPM", "Value": 200000, "Unit": "Tokens/minute", "Adjustable": True}},
            {"Quota": {"QuotaName": "Claude Sonnet 4 RPM", "Value": 2000, "Unit": "Requests/minute", "Adjustable": True}},
        ])

        quota_codes = [
            {"quota_code": "L-AAA"},
            {"quota_code": "L-BBB"},
        ]

        with self._patch_boto3_client(mock_client):
            result = _mod._fetch_live_quota_values("us-east-1", quota_codes)

        assert len(result) == 2
        assert result[0] == {
            "name": "Claude Sonnet 4 TPM",
            "value": 200000,
            "unit": "Tokens/minute",
            "adjustable": True,
        }
        assert result[1] == {
            "name": "Claude Sonnet 4 RPM",
            "value": 2000,
            "unit": "Requests/minute",
            "adjustable": True,
        }

    def test_no_such_resource_skipped(self):
        """NoSuchResourceException quota codes are silently skipped."""
        no_resource_error = ClientError(
            {"Error": {"Code": "NoSuchResourceException", "Message": "not found"}},
            "GetServiceQuota",
        )
        mock_client = self._make_mock_sq_client(side_effects=[
            no_resource_error,
            {"Quota": {"QuotaName": "Nova Pro TPM", "Value": 50000, "Unit": "", "Adjustable": False}},
        ])

        quota_codes = [
            {"quota_code": "L-GONE"},
            {"quota_code": "L-GOOD"},
        ]

        with self._patch_boto3_client(mock_client):
            result = _mod._fetch_live_quota_values("us-east-1", quota_codes)

        assert len(result) == 1
        assert result[0]["name"] == "Nova Pro TPM"

    def test_throttled_returns_throttled_marker(self):
        """TooManyRequestsException returns a THROTTLED marker entry."""
        throttle_error = ClientError(
            {"Error": {"Code": "TooManyRequestsException", "Message": "slow down"}},
            "GetServiceQuota",
        )
        mock_client = self._make_mock_sq_client(side_effects=[throttle_error])

        quota_codes = [{"quota_code": "L-THROTTLE", "quota_name": "Throttled Quota"}]

        with self._patch_boto3_client(mock_client):
            result = _mod._fetch_live_quota_values("us-west-2", quota_codes)

        assert len(result) == 1
        assert result[0]["value"] == "THROTTLED"
        assert result[0]["name"] == "Throttled Quota"

    def test_throttling_exception_also_returns_marker(self):
        """ThrottlingException (alternative code) also returns THROTTLED marker."""
        throttle_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "rate exceeded"}},
            "GetServiceQuota",
        )
        mock_client = self._make_mock_sq_client(side_effects=[throttle_error])

        quota_codes = [{"quota_code": "L-RATE", "quota_name": "Rate Limited"}]

        with self._patch_boto3_client(mock_client):
            result = _mod._fetch_live_quota_values("us-east-1", quota_codes)

        assert len(result) == 1
        assert result[0]["value"] == "THROTTLED"

    def test_other_client_error_raises(self):
        """Non-throttle, non-NoSuchResource errors are re-raised."""
        access_error = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "forbidden"}},
            "GetServiceQuota",
        )
        mock_client = self._make_mock_sq_client(side_effects=[access_error])

        quota_codes = [{"quota_code": "L-DENIED"}]

        with self._patch_boto3_client(mock_client):
            with pytest.raises(ClientError) as exc_info:
                _mod._fetch_live_quota_values("us-east-1", quota_codes)
            assert "AccessDeniedException" in str(exc_info.value)

    def test_empty_quota_codes(self):
        """Empty quota_codes list returns empty results."""
        mock_client = self._make_mock_sq_client(responses=[])

        with self._patch_boto3_client(mock_client):
            result = _mod._fetch_live_quota_values("us-east-1", [])

        assert result == []
        mock_client.get_service_quota.assert_not_called()

    def test_missing_quota_fields_default(self):
        """Missing fields in the Quota response use defaults."""
        mock_client = self._make_mock_sq_client(responses=[
            {"Quota": {}},  # All fields missing
        ])

        quota_codes = [{"quota_code": "L-MINIMAL"}]

        with self._patch_boto3_client(mock_client):
            result = _mod._fetch_live_quota_values("us-east-1", quota_codes)

        assert len(result) == 1
        assert result[0] == {
            "name": "Unknown",
            "value": 0,
            "unit": "",
            "adjustable": False,
        }


# ---------------------------------------------------------------------------
# Tests: _fallback_paginate_quotas (ServiceQuotas paginator mock)
# ---------------------------------------------------------------------------


class TestFallbackPaginateQuotas:
    """Tests for _fallback_paginate_quotas with mocked paginator."""

    def _make_mock_paginator(self, pages):
        """Create a mock paginator that yields the given pages.

        Args:
            pages: list of lists of quota dicts (each list is one page).
        """
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Quotas": page_quotas} for page_quotas in pages
        ]
        return mock_paginator

    def _patch_boto3_client_with_paginator(self, mock_paginator):
        """Patch boto3.client to return a client with our mock paginator."""
        mock_client = MagicMock()
        mock_client.get_paginator.return_value = mock_paginator
        return patch.object(_mod, "boto3", **{"client.return_value": mock_client})

    def test_returns_all_quotas_formatted(self):
        """All quotas from all pages are formatted into a single string."""
        quotas_page1 = [
            {"QuotaName": "Claude Sonnet 4 TPM", "Value": 200000, "Unit": "Tokens/minute", "Adjustable": True},
            {"QuotaName": "Claude Sonnet 4 RPM", "Value": 2000, "Unit": "Requests/minute", "Adjustable": True},
        ]
        quotas_page2 = [
            {"QuotaName": "Nova Pro TPM", "Value": 50000, "Unit": "Tokens/minute", "Adjustable": False},
        ]
        mock_paginator = self._make_mock_paginator([quotas_page1, quotas_page2])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("us-east-1")

        assert "Found 3 quotas" in result
        assert "Claude Sonnet 4 TPM" in result
        assert "Claude Sonnet 4 RPM" in result
        assert "Nova Pro TPM" in result
        assert "200000" in result
        assert "Adjustable: Yes" in result
        assert "Adjustable: No" in result

    def test_model_filter_applied(self):
        """model_filter restricts results to matching quotas."""
        quotas = [
            {"QuotaName": "Claude Sonnet 4 TPM", "Value": 200000, "Unit": "Tokens/minute", "Adjustable": True},
            {"QuotaName": "Nova Pro TPM", "Value": 50000, "Unit": "Tokens/minute", "Adjustable": False},
            {"QuotaName": "Claude Sonnet 4 RPM", "Value": 2000, "Unit": "", "Adjustable": True},
        ]
        mock_paginator = self._make_mock_paginator([quotas])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("us-east-1", model_filter="Claude Sonnet 4")

        assert "Found 2 quotas" in result
        assert "Claude Sonnet 4 TPM" in result
        assert "Claude Sonnet 4 RPM" in result
        assert "Nova Pro" not in result
        assert "(Filtered by: Claude Sonnet 4)" in result

    def test_model_filter_case_insensitive(self):
        """model_filter comparison is case-insensitive."""
        quotas = [
            {"QuotaName": "CLAUDE SONNET 4 TPM", "Value": 100000, "Unit": "", "Adjustable": True},
        ]
        mock_paginator = self._make_mock_paginator([quotas])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("us-west-2", model_filter="claude sonnet 4")

        assert "Found 1 quotas" in result
        assert "CLAUDE SONNET 4 TPM" in result

    def test_no_quotas_found(self):
        """Empty result reports 0 quotas found."""
        mock_paginator = self._make_mock_paginator([[]])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("eu-west-1")

        assert "Found 0 quotas" in result

    def test_region_in_header(self):
        """The region name appears in the output header."""
        mock_paginator = self._make_mock_paginator([[]])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("ap-southeast-1")

        assert "ap-southeast-1" in result

    def test_unit_none_not_displayed(self):
        """When Unit is 'None' (string) or empty, it is not shown."""
        quotas = [
            {"QuotaName": "Test Quota", "Value": 42, "Unit": "None", "Adjustable": False},
        ]
        mock_paginator = self._make_mock_paginator([quotas])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("us-east-1")

        # The Unit "None" should not appear after the value
        lines = result.split("\n")
        value_line = [line for line in lines if "Current Value: 42" in line][0]
        assert "None" not in value_line

    def test_unit_displayed_when_valid(self):
        """When Unit is a real value like 'Tokens/minute', it is shown."""
        quotas = [
            {"QuotaName": "TPM Quota", "Value": 100000, "Unit": "Tokens/minute", "Adjustable": True},
        ]
        mock_paginator = self._make_mock_paginator([quotas])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("us-east-1")

        assert "Tokens/minute" in result

    def test_cache_unavailable_header(self):
        """Output header indicates 'direct API - cache unavailable'."""
        mock_paginator = self._make_mock_paginator([[]])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("us-east-1")

        assert "direct API - cache unavailable" in result

    def test_multiple_pages_combined(self):
        """Quotas from multiple pages are combined correctly."""
        page1 = [{"QuotaName": f"Quota {i}", "Value": i * 100, "Unit": "", "Adjustable": True} for i in range(3)]
        page2 = [{"QuotaName": f"Quota {i}", "Value": i * 100, "Unit": "", "Adjustable": False} for i in range(3, 6)]
        mock_paginator = self._make_mock_paginator([page1, page2])

        with self._patch_boto3_client_with_paginator(mock_paginator):
            result = _mod._fallback_paginate_quotas("us-east-1")

        assert "Found 6 quotas" in result
        for i in range(6):
            assert f"Quota {i}" in result
