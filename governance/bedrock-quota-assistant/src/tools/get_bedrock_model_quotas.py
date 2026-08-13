# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: get_bedrock_model_quotas — fetches live Bedrock quota values from Service Quotas."""

import re
import logging

from strands import tool

from config import DEFAULT_REGION, QUOTA_CACHE_TABLE
from helpers.quota_cache import (
    _query_quota_codes,
    _fetch_live_quota_values,
    _filter_strict_model_match,
    _fallback_paginate_quotas,
)

logger = logging.getLogger(__name__)


@tool
def get_bedrock_model_quotas(region: str = DEFAULT_REGION, model_filter: str = None) -> str:
    """Get current Bedrock model quotas for the AWS account.

    Uses cached quota codes from DynamoDB for fast lookup, then fetches live values
    via GetServiceQuota. No stale data - values are always current.

    Args:
        region: AWS region to check quotas in (default: agent's deployed region)
        model_filter: Filter to show only quotas matching this model name (e.g., "haiku", "claude", "nova")

    Returns:
        String with quota information for Bedrock models
    """
    try:
        # Step 1: Look up quota codes from DynamoDB
        quota_codes = _query_quota_codes(model_filter)

        if not quota_codes:
            # DynamoDB cache empty or unavailable - fall back to pagination
            return _fallback_paginate_quotas(region, model_filter)

        # Step 2: Guard against overly broad queries
        MAX_LIVE_LOOKUPS = 50
        if len(quota_codes) > MAX_LIVE_LOOKUPS:
            return (
                f"Found {len(quota_codes)} matching quotas, which is too many to fetch live values for. "
                f"Please narrow your search with a more specific model name "
                f"(e.g., 'claude sonnet 4', 'nova pro', 'haiku')."
            )

        # Step 3: Fetch live values for each quota code in the requested region
        results = _fetch_live_quota_values(region, quota_codes)

        # Step 3b: Strict model filter to exclude sub-version matches
        # e.g., "sonnet 4" should not return "sonnet 4.5" or "sonnet 4.6"
        if model_filter and results:
            # Normalise the filter the same way as _query_quota_codes does
            # so "claude sonnet 4-6" becomes "claude sonnet 4.6" for matching
            normalised_filter = model_filter.lower().strip().rstrip(".,;:!?").replace("-", " ").replace("_", " ")
            normalised_filter = re.sub(r"(\d) (\d)(?=\s|$)", r"\1.\2", normalised_filter)
            results = _filter_strict_model_match(results, normalised_filter)

        if not results:
            return (
                f"No Bedrock quotas found in {region}"
                + (f" matching '{model_filter}'" if model_filter else "")
                + ". The model may not be available in this region."
            )

        # Step 4: Format response
        quotas_info = [f"Bedrock Quotas in {region}:"]
        if model_filter:
            quotas_info.append(f"(Filtered by: {model_filter})")
        quotas_info.append(f"Found {len(results)} quotas")
        quotas_info.append("=" * 80)

        for quota in results:
            quotas_info.append(f"\n{quota['name']}")
            if quota["value"] == "THROTTLED":
                quotas_info.append("  Current Value: Unable to fetch (rate limited)")
            else:
                quotas_info.append(f"  Current Value: {quota['value']}{' ' + quota['unit'] if quota['unit'] and quota['unit'] != 'None' else ''}")
            quotas_info.append(f"  Adjustable: {'Yes' if quota['adjustable'] else 'No'}")

        return "\n".join(quotas_info)

    except Exception as e:
        logger.error(f"Error retrieving Bedrock quotas: {e}", exc_info=True)
        return f"Error retrieving Bedrock quotas: {str(e)}\n\nNote: Make sure DynamoDB table '{QUOTA_CACHE_TABLE}' exists and the agent has proper permissions."
