# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: get_customer_profile — returns a compact overview of the account's Bedrock setup."""

from strands import tool

from helpers.snapshot import get_snapshot_cached, _format_for_llm


@tool
def get_customer_profile() -> str:
    """Get a summary of all Bedrock models, quotas, and inference profiles in this account.

    Returns a compact overview showing:
    - Quota groups with TPM/RPM limits and activity levels
    - Full model inventory with access patterns
    - Application inference profiles and what they wrap

    Use this when the user asks about their overall Bedrock setup, which models
    they have, what profiles exist, or when you need to resolve a model name
    that other tools can't find.
    """
    snapshot = get_snapshot_cached()
    if not snapshot:
        return (
            "Customer profile not yet available. The cache refresh Lambda "
            "has not run yet. Ask your admin to invoke the refresh Lambda, "
            "or wait for the next scheduled run."
        )
    return _format_for_llm(snapshot)
