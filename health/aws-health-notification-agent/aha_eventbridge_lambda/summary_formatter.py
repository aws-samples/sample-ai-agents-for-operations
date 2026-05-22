# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Format Agent_Classification_Result dicts as plain-text summaries."""


def format_summary(classification_result: dict) -> str:
    """Construct a Human_Readable_Summary from the agent's output.

    Handles two formats:
    1. Full agent output with "notifications" array (from the agent's JSON output)
    2. Single classification result dict (legacy format)

    Returns plain-text summary string (not JSON).
    """
    # If the result has a "notifications" array, format each notification
    if "notifications" in classification_result and isinstance(classification_result["notifications"], list):
        return _format_full_output(classification_result)

    # Single classification result
    return _format_single(classification_result)


def _format_full_output(result: dict) -> str:
    """Format the full agent output with notifications array."""
    lines = []
    lines.append(f"AWS Health Notification Classification Summary")
    lines.append(f"Status: {result.get('status', 'unknown')}")
    lines.append(f"Total: {result.get('total_count', 0)} notification(s)")
    lines.append(f"  Service Disruptions: {result.get('service_disruption_count', 0)}")
    lines.append(f"  Breaking Changes: {result.get('breaking_change_count', 0)}")
    lines.append(f"  Security Related: {result.get('security_related_count', 0)}")
    lines.append(f"  Cost Implications: {result.get('cost_implication_count', 0)}")
    lines.append(f"  Informational: {result.get('informational_count', 0)}")
    lines.append(f"  Unclassified: {result.get('unclassified_count', 0)}")
    lines.append(f"SNS Publish: {result.get('sns_publish_status', 'unknown')}")
    lines.append("")

    for i, notif in enumerate(result.get("notifications", []), 1):
        lines.append(f"--- Notification {i} ---")
        lines.append(f"Classification: {notif.get('classification', '')}")
        urgency = notif.get("urgency", "")
        if urgency:
            lines.append(f"Urgency: {urgency.upper()}")
        deadline = notif.get("deadline")
        if deadline:
            lines.append(f"Deadline: {deadline}")
        lines.append(f"Reason: {notif.get('reason', '')}")
        lines.append(f"Event Type: {notif.get('event_type', '')}")
        lines.append(f"Affected Service: {notif.get('affected_service', '')}")

        lines.append("")
        lines.append("Affected Accounts:")
        for acct in notif.get("affected_accounts", []):
            if isinstance(acct, dict):
                acct_id = acct.get("account_id", "")
                acct_name = acct.get("account_name", "")
                env_type = acct.get("environment_type", "")
                name_part = f" ({acct_name})" if acct_name and acct_name != acct_id else ""
                lines.append(f"  - {acct_id}{name_part} [{env_type}]")

        bd = notif.get("environment_breakdown", {})
        if bd:
            lines.append(f"  Production: {bd.get('production_count', 0)}, Non-production: {bd.get('non_production_count', 0)}")

        impact = notif.get("impact_analysis")
        if impact and isinstance(impact, dict):
            lines.append("")
            lines.append("Impact Analysis:")
            impact_status = impact.get("impact_status", "unknown")
            lines.append(f"  Impact Status: {impact_status.upper()}")
            lines.append(f"  {impact.get('summary', '')}")
            lines.append(f"  Risk Level: {impact.get('risk_level', '')}")
            action = "Yes" if impact.get("action_required") else "No"
            lines.append(f"  Action Required: {action}")

            steps = impact.get("suggested_next_steps")
            if steps and isinstance(steps, list):
                lines.append("")
                if impact_status == "confirmed":
                    lines.append("  Suggested Remediation Steps:")
                else:
                    lines.append("  Suggested Verification Steps:")
                for j, step in enumerate(steps, 1):
                    lines.append(f"    {j}. {step}")

        cost = notif.get("cost_projection")
        if cost and isinstance(cost, dict):
            lines.append("")
            lines.append("Cost Projection:")
            if cost.get("projectable"):
                lines.append(f"  Org Total: ${cost.get('org_total_projected_cost', 0):,.2f} {cost.get('currency', 'USD')}")
            else:
                lines.append(f"  {cost.get('reason', 'Cost projection unavailable')}")

        lines.append("")

    return "\n".join(lines)


def _format_single(classification_result: dict) -> str:
    """Format a single classification result (legacy format)."""
    lines = []

    # Support both key naming conventions
    category = classification_result.get("classification_category", classification_result.get("classification", ""))
    reason = classification_result.get("classification_reason", classification_result.get("reason", ""))
    service = classification_result.get("affected_service", "")

    lines.append(f"Classification: {category}")
    lines.append(f"Reason: {reason}")
    lines.append(f"Affected Service: {service}")

    lines.append("")
    lines.append("Affected Accounts:")
    for acct in classification_result.get("affected_accounts", []):
        if isinstance(acct, dict):
            acct_id = acct.get("account_id", "")
            env_type = acct.get("environment_type", "")
            lines.append(f"  - {acct_id} ({env_type})")

    impact = classification_result.get("impact_analysis")
    if impact and isinstance(impact, dict):
        lines.append("")
        lines.append("Impact Analysis:")
        impact_status = impact.get("impact_status", "unknown")
        lines.append(f"  Impact Status: {impact_status.upper()}")
        lines.append(f"  Summary: {impact.get('summary', '')}")
        lines.append(f"  Risk Level: {impact.get('risk_level', '')}")
        action = "Yes" if impact.get("action_required") else "No"
        lines.append(f"  Action Required: {action}")

        steps = impact.get("suggested_next_steps")
        if steps and isinstance(steps, list):
            lines.append("")
            if impact_status == "confirmed":
                lines.append("  Suggested Remediation Steps:")
            else:
                lines.append("  Suggested Verification Steps:")
            for j, step in enumerate(steps, 1):
                lines.append(f"    {j}. {step}")

    cost = classification_result.get("cost_projection")
    if cost and isinstance(cost, dict):
        lines.append("")
        lines.append("Cost Projection:")
        lines.append(f"  {cost.get('details', '')}")

    return "\n".join(lines)
