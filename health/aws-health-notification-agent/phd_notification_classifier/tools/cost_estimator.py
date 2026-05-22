# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Cost Estimator tool.

Produces per-account and organization-wide cost projections for
COST_IMPLICATION notifications.  Tracks historical cost data for
similar events to improve projection accuracy over time.
"""

from __future__ import annotations

from strands import tool

# In-memory historical cost store keyed by eventTypeCode.
# In production this would be backed by a persistent store (DynamoDB, S3, etc.).
_historical_costs: dict[str, list[float]] = {}

# Known per-resource monthly cost estimates by service (USD).
# A real implementation would pull these from a pricing API or config.
KNOWN_SERVICE_COSTS: dict[str, float] = {
    "EKS": 500.0,
    "RDS": 300.0,
    "LAMBDA": 50.0,
    "CASSANDRA": 400.0,  # AWS Health internal code for Amazon Keyspaces (for Apache Cassandra)
    "EC2": 200.0,
    "ELASTICACHE": 250.0,
    "OPENSEARCH": 350.0,
}


def _per_account_cost(service: str, resources: list[str]) -> float | None:
    """Estimate cost for one account based on service and resource count.

    Returns None when the service is not in the known cost table.
    """
    base = KNOWN_SERVICE_COSTS.get(service)
    if base is None:
        return None
    count = max(len(resources), 1)  # at least 1 resource implied
    return round(base * count, 2)


def _record_historical(event_type: str, total: float) -> None:
    """Persist a cost data point for future reference."""
    _historical_costs.setdefault(event_type, []).append(total)


def _historical_reference(event_type: str) -> str | None:
    """Return a human-readable reference to past similar events, if any."""
    history = _historical_costs.get(event_type)
    if not history:
        return None
    avg = sum(history) / len(history)
    return (
        f"Based on {len(history)} similar past event(s), "
        f"average projected cost was ${avg:,.2f} USD."
    )


@tool
def estimate_cost(notification: dict, affected_accounts: list) -> dict:
    """Estimate cost impact for a COST_IMPLICATION notification.

    Produces per-account and organization-wide cost projections.
    Tracks historical data for similar events to improve accuracy.
    Returns ``projectable: false`` with a reason when cost cannot be
    determined.

    Args:
        notification: A notification dict with at least ``arn``,
            ``service``, ``eventTypeCode``.
        affected_accounts: List of account-ID strings or dicts with
            ``account_id`` and optional ``affected_resources``.

    Returns:
        A CostProjection dict.
    """
    try:
        notif_id = (
            notification.get("arn")
            or notification.get("notification_id", "unknown")
        )
        service = notification.get("service", "")
        event_type = notification.get("eventTypeCode", "")
    except (AttributeError, TypeError) as exc:
        return {
            "notification_id": "unknown",
            "projectable": False,
            "per_account_costs": [],
            "org_total_projected_cost": None,
            "currency": "USD",
            "reason": f"Invalid notification format: {exc}",
            "historical_reference": None,
        }

    if not service:
        return {
            "notification_id": notif_id,
            "projectable": False,
            "per_account_costs": [],
            "org_total_projected_cost": None,
            "currency": "USD",
            "reason": "Service name missing from notification.",
            "historical_reference": _historical_reference(event_type),
        }

    # Normalise accounts
    normalised: list[dict] = []
    for acct in affected_accounts:
        if isinstance(acct, str):
            normalised.append({"account_id": acct, "affected_resources": []})
        else:
            normalised.append({
                "account_id": acct.get("account_id", "unknown"),
                "affected_resources": acct.get("affected_resources", []),
            })

    per_account_costs: list[dict] = []
    all_projectable = True

    for acct in normalised:
        cost = _per_account_cost(service, acct["affected_resources"])
        if cost is None:
            all_projectable = False
        per_account_costs.append({
            "account_id": acct["account_id"],
            "projected_cost": cost,
            "currency": "USD",
        })

    # If no accounts or service unknown for all → not projectable
    if not normalised:
        return {
            "notification_id": notif_id,
            "projectable": False,
            "per_account_costs": [],
            "org_total_projected_cost": None,
            "currency": "USD",
            "reason": "No affected accounts provided.",
            "historical_reference": _historical_reference(event_type),
        }

    if not all_projectable:
        return {
            "notification_id": notif_id,
            "projectable": False,
            "per_account_costs": per_account_costs,
            "org_total_projected_cost": None,
            "currency": "USD",
            "reason": (
                f"Cost data unavailable for service '{service}'. "
                "Unable to produce a reliable projection."
            ),
            "historical_reference": _historical_reference(event_type),
        }

    org_total = round(
        sum(c["projected_cost"] for c in per_account_costs), 2
    )

    # Record for future historical reference
    _record_historical(event_type, org_total)

    return {
        "notification_id": notif_id,
        "projectable": True,
        "per_account_costs": per_account_costs,
        "org_total_projected_cost": org_total,
        "currency": "USD",
        "reason": None,
        "historical_reference": _historical_reference(event_type),
    }
