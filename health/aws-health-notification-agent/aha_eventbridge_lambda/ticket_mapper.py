# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Map agent notification dicts to Jira issue fields.

Transforms a single notification from the Classification_Result into a dict
suitable for the Jira REST API v2 POST /rest/api/2/issue endpoint.
Uses Jira wiki markup for the description body.
"""

from __future__ import annotations

import re

# Risk level → Jira priority name
RISK_TO_PRIORITY: dict[str, str] = {
    "high": "Highest",
    "medium": "High",
    "low": "Medium",
}


def _sanitize_label(value: str) -> str:
    """Sanitize a string for use as a Jira label.

    Replaces spaces with underscores, strips characters that are not
    alphanumeric, hyphens, underscores, colons, or forward-slashes,
    and truncates to 255 characters.
    """
    value = value.replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9\-_:/]", "", value)
    return value[:255]


def format_description_wiki(notification: dict) -> str:
    """Render a notification dict as Jira wiki markup.

    Sections: Event Details, Classification, Affected Accounts (table),
    Impact Analysis, Remediation Steps, and optionally Cost Projection.
    """
    lines: list[str] = []

    # Event Details
    lines.append("h2. Event Details")
    lines.append(f"*Event ARN:* {notification.get('event_arn', '')}")
    lines.append(f"*Event Type:* {notification.get('event_type', '')}")
    lines.append("")

    # Classification
    lines.append("h2. Classification")
    lines.append(f"*Category:* {notification.get('classification', '')}")
    lines.append(f"*Reason:* {notification.get('reason', '')}")
    lines.append("")

    # Affected Accounts table
    lines.append("h2. Affected Accounts")
    lines.append("||Account ID||Account Name||Environment||Resources||")
    for acct in notification.get("affected_accounts", []):
        acct_id = acct.get("account_id", "")
        acct_name = acct.get("account_name", acct_id)
        env = acct.get("environment_type", "")
        resources = ", ".join(acct.get("resources", [])) if isinstance(acct.get("resources"), list) else str(acct.get("resources", ""))
        lines.append(f"|{acct_id}|{acct_name}|{env}|{resources}|")
    lines.append("")

    # Impact Analysis
    impact = notification.get("impact_analysis", {}) or {}
    lines.append("h2. Impact Analysis")
    lines.append(f"*Summary:* {impact.get('summary', '')}")
    lines.append(f"*Risk Level:* {impact.get('risk_level', '')}")
    lines.append(f"*Impact Status:* {impact.get('impact_status', '')}")
    lines.append("")

    # Remediation Steps
    lines.append("h2. Remediation Steps")
    for step in impact.get("suggested_next_steps", []):
        lines.append(f"# {step}")
    lines.append("")

    # Cost Projection (conditional)
    cost = notification.get("cost_projection")
    if cost and isinstance(cost, dict):
        lines.append("h2. Cost Projection")
        if cost.get("projectable"):
            lines.append(f"*Org Total:* ${cost.get('org_total_projected_cost', 0):,.2f} {cost.get('currency', 'USD')}")
        if cost.get("reason"):
            lines.append(f"*Reason:* {cost['reason']}")
        if cost.get("details"):
            lines.append(f"*Details:* {cost['details']}")
        lines.append("")

    return "\n".join(lines)


def map_notification_to_jira_fields(
    notification: dict,
    project_key: str,
    issue_type: str,
    assignee: str | None = None,
    component: str | None = None,
    team_id: str | None = None,
) -> dict:
    """Transform a notification dict into Jira issue create fields.

    Args:
        notification: Single notification dict from the agent output.
        project_key: Jira project key (e.g. "OPS").
        issue_type: Jira issue type name (e.g. "Task").
        assignee: Optional Jira assignee account ID or team name.
        component: Optional Jira component name.
        team_id: Optional Jira team ID (UUID) for customfield_10001.

    Returns:
        Dict with a "fields" key ready for JiraClient.create_issue().
    """
    classification = notification.get("classification", "")
    affected_service = notification.get("affected_service", "")
    event_arn = notification.get("event_arn", "")

    # Summary: "{classification}: {affected_service}" truncated to 255
    summary = f"{classification}: {affected_service}"[:255]

    # Description in Jira wiki markup
    description = format_description_wiki(notification)

    # Priority from risk_level
    impact = notification.get("impact_analysis", {}) or {}
    risk_level = (impact.get("risk_level", "") or "").lower()
    priority_name = RISK_TO_PRIORITY.get(risk_level, "Medium")

    # Labels
    labels = [
        _sanitize_label(classification),
        _sanitize_label(affected_service),
        "phd-auto-created",
        _sanitize_label(event_arn),
    ]
    # Remove empty labels
    labels = [lbl for lbl in labels if lbl]

    fields: dict = {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
        "summary": summary,
        "description": description,
        "priority": {"name": priority_name},
        "labels": labels,
    }

    if assignee:
        # If assignee looks like a Jira account ID (contains digits/hyphens), set as assignee
        # Otherwise treat it as a team name — add as label and prepend to description
        if re.match(r"^[0-9a-f:-]+$", assignee):
            fields["assignee"] = {"accountId": assignee}
        else:
            fields["labels"].append(_sanitize_label(f"team-{assignee}"))
            fields["description"] = f"*Assigned Team:* {assignee}\n\n{fields['description']}"

    if team_id:
        fields["customfield_10001"] = team_id

    if component:
        fields["components"] = [{"name": component}]

    return {"fields": fields}
