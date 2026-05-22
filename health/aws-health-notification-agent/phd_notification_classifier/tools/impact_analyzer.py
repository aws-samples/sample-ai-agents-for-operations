# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Impact Analyzer tool.

Assesses the impact of BREAKING_CHANGE notifications across affected accounts
and environments, producing risk-scored impact summaries with required actions.

Security & Compliance:
    - Data classification: Notification data = CONFIDENTIAL, Account IDs = CONFIDENTIAL.
      See security/SECURITY.md "Data Classification and Handling" section.
    - Encryption at rest: Lambda env vars encrypted with AWS KMS CMK (KmsKeyArn in template).
      SNS topic encrypted with CMK. See security/SECURITY.md "Key Management Strategy".
    - Access logging: CloudTrail logs all Lambda invocations and Organizations API calls.
      CloudWatch Logs with 90-day retention. See security/SECURITY.md "Access Logging".
    - Key management: Customer-managed AWS KMS key with annual rotation.
      See security/SECURITY_GUIDELINES.md for per-service guidelines.

The affected_accounts parameter accepts enriched account context dicts
(from get_account_context) containing account_id, account_name,
environment_type, and affected_resources.
"""

from __future__ import annotations

from strands import tool


def _risk_score(env_type: str) -> int:
    """Numeric risk score: production > non-production."""
    return 3 if env_type == "production" else 1


def _risk_level(accounts: list[dict]) -> str:
    """Derive an overall risk level from affected account details.

    - high: any production account affected
    - medium: multiple non-production accounts affected
    - low: single non-production account or none
    """
    if any(a["environment_type"] == "production" for a in accounts):
        return "high"
    if len(accounts) > 1:
        return "medium"
    return "low"


@tool
def analyze_impact(notification: dict, affected_accounts: list) -> dict:
    """Analyze the impact of a BREAKING_CHANGE notification.

    Inspects affected accounts and resources, assigns risk based on
    environment type (production receives higher risk), and produces an
    impact summary with required actions per account.

    Args:
        notification: A notification or consolidated-view dict containing at
            least ``arn`` (or ``notification_id``), ``service``,
            ``eventTypeCode``, and ``eventDescription``.
        affected_accounts: List of enriched account context dicts with
            ``account_id``, ``account_name``, ``environment_type``, and
            optional ``affected_resources``.

    Returns:
        An ImpactAnalysis dict.
    """
    notif_id = notification.get("arn") or notification.get("notification_id", "unknown")
    service = notification.get("service", "unknown")
    description = notification.get("eventDescription", "")

    # Normalise affected_accounts to list-of-dicts with enriched context
    normalised: list[dict] = []
    for acct in affected_accounts:
        if isinstance(acct, str):
            # Fallback for plain account ID strings (legacy callers)
            normalised.append({
                "account_id": acct,
                "account_name": acct,
                "environment_type": "unknown",
                "affected_resources": [],
            })
        else:
            normalised.append({
                "account_id": acct.get("account_id", "unknown"),
                "account_name": acct.get("account_name", acct.get("account_id", "unknown")),
                "environment_type": acct.get("environment_type", "unknown"),
                "affected_resources": acct.get("affected_resources", []),
            })

    # Build per-account impact details
    account_details: list[dict] = []
    for acct in normalised:
        env = acct["environment_type"]
        resources = acct["affected_resources"]
        action = (
            f"Review and remediate {service} resources before the change takes effect."
            if resources
            else f"Verify {service} usage and take action if applicable."
        )
        account_details.append({
            "account_id": acct["account_id"],
            "environment_type": env,
            "risk_score": _risk_score(env),
            "affected_resources": resources,
            "required_action": action,
        })

    # No affected accounts at all → no action required
    if not account_details:
        return {
            "notification_id": notif_id,
            "action_required": False,
            "risk_level": "low",
            "impact_status": "confirmed",
            "affected_accounts": [],
            "summary": f"No affected accounts found for {service} event. No action required.",
            "suggested_next_steps": None,
        }

    risk = _risk_level(account_details)
    prod_count = sum(1 for a in account_details if a["environment_type"] == "production")
    non_prod_count = len(account_details) - prod_count

    summary_parts = [
        f"{service} breaking change affects {len(account_details)} account(s)",
        f"({prod_count} production, {non_prod_count} non-production).",
    ]
    if description:
        summary_parts.append(f"Details: {description}")

    return {
        "notification_id": notif_id,
        "action_required": True,
        "risk_level": risk,
        "impact_status": "unconfirmed",
        "affected_accounts": account_details,
        "summary": " ".join(summary_parts),
        "suggested_next_steps": [
            f"Verify {service} usage in each affected account and check if your workloads are impacted by this change.",
            f"Review the notification details and determine if your {service} configuration requires updates.",
            "Check with your application teams to confirm whether remediation is needed.",
        ],
    }
