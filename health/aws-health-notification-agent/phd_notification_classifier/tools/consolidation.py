# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Notification consolidation tool.

Groups related PHD notifications across accounts into consolidated views
with account-level detail, environment breakdowns, and org-wide summaries.

Notifications are expected to have been enriched with account context
(account_name, environment_type) from get_account_context before
consolidation.
"""

from __future__ import annotations

from strands import tool


def _event_key(notification: dict) -> str:
    """Derive a grouping key for a notification.

    Notifications are considered related when they share the same event ARN
    **or** the same (eventTypeCode, service) pair.  We prefer the composite
    key so that events with different ARNs but the same type+service are
    still grouped together.
    """
    event_type = notification.get("eventTypeCode", "")
    service = notification.get("service", "")
    if event_type and service:
        return f"{event_type}::{service}"
    return notification.get("arn", "")


def _normalize_account(acct) -> dict:
    """Normalize an account entry to a dict with required fields.

    Accepts either:
    - A dict with enriched account context (account_id, account_name, environment_type)
    - A plain string account ID (fallback for unenriched notifications)
    """
    if isinstance(acct, dict):
        return {
            "account_id": acct.get("account_id", ""),
            "account_name": acct.get("account_name", acct.get("account_id", "")),
            "environment_type": acct.get("environment_type", "unknown"),
            "affected_resources": acct.get("affected_resources", []),
        }
    # Plain string account ID — unenriched fallback
    return {
        "account_id": str(acct),
        "account_name": str(acct),
        "environment_type": "unknown",
        "affected_resources": [],
    }


def _build_org_impact_summary(view: dict) -> str:
    """Generate a human-readable organization-wide impact summary."""
    prod = view["environment_breakdown"]["production_count"]
    non_prod = view["environment_breakdown"]["non_production_count"]
    total = prod + non_prod
    service = view.get("service", "unknown service")
    event_type = view.get("eventTypeCode", "unknown event")

    parts = [
        f"{event_type} affects {total} account(s) using {service}",
        f"({prod} production, {non_prod} non-production).",
    ]
    return " ".join(parts)


@tool
def consolidate_notifications(notifications: list) -> list:
    """Consolidate related PHD notifications across accounts into unified views.

    Groups notifications by the same health event (matching event type code
    and service).  Produces a ConsolidatedView per unique event with
    account-level detail, production/non-production environment breakdown,
    and an organization-wide impact summary.

    Notifications should have enriched affectedAccounts entries (dicts with
    account_id, account_name, environment_type from get_account_context).
    Plain string account IDs are also accepted as a fallback.

    Calling this function again with additional notifications will update
    existing views rather than creating duplicates.

    Args:
        notifications: List of notification dicts, each with affectedAccounts
            enriched with account context.

    Returns:
        List of ConsolidatedView dicts.
    """
    views: dict[str, dict] = {}

    for notif in notifications:
        key = _event_key(notif)
        arn = notif.get("arn", "")
        accounts = notif.get("affectedAccounts", [])

        if key not in views:
            views[key] = {
                "event_key": key,
                "event_arns": [],
                "service": notif.get("service", ""),
                "eventTypeCode": notif.get("eventTypeCode", ""),
                "eventDescription": notif.get("eventDescription", ""),
                "affected_accounts": [],
                "environment_breakdown": {
                    "production_count": 0,
                    "non_production_count": 0,
                },
                "org_impact_summary": "",
            }

        view = views[key]

        # Track ARNs (deduplicate)
        if arn and arn not in view["event_arns"]:
            view["event_arns"].append(arn)

        # Prefer the longest / most informative description
        desc = notif.get("eventDescription", "")
        if len(desc) > len(view["eventDescription"]):
            view["eventDescription"] = desc

        # Merge affected accounts (deduplicate by account_id)
        existing_ids = {a["account_id"] for a in view["affected_accounts"]}
        for acct in accounts:
            normalized = _normalize_account(acct)
            acct_id = normalized["account_id"]
            if acct_id not in existing_ids:
                view["affected_accounts"].append(normalized)
                existing_ids.add(acct_id)
                env = normalized["environment_type"]
                if env == "production":
                    view["environment_breakdown"]["production_count"] += 1
                else:
                    view["environment_breakdown"]["non_production_count"] += 1

    # Build org impact summaries after all notifications are merged
    result = list(views.values())
    for view in result:
        view["org_impact_summary"] = _build_org_impact_summary(view)

    return result
