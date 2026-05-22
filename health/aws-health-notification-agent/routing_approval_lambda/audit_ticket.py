# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Jira audit ticket creation for routing config changes.

Risk Assessment Reference:
    See security/SECURITY.md for the full risk assessment covering:
    - Security risks: Webhook injection, token security, data exposure (Threats T-001 to T-010)
    - Compliance: AWS service quotas, data retention (7-day TTL), audit trails (CloudTrail)
    - Operational risks: Lambda timeout (900s), DynamoDB throttling (PAY_PER_REQUEST),
      Jira API rate limiting (handled via error responses)
    - Mitigations: M-001 to M-016 with implementation code examples
    - Residual risks: Documented in Accepted Risks table with compensating controls
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def build_audit_ticket_fields(
    routing_json: dict,
    source_file: str,
    approver: str,
    project_key: str,
    issue_type: str,
) -> dict:
    """Build Jira issue fields for a routing config audit ticket.

    Pure function exposed for testability (Property 8).

    Returns a dict with a ``fields`` key ready for ``JiraClient.create_issue()``.
    """
    service_count = len(routing_json.get("by_service", {}))
    ou_count = len(routing_json.get("by_ou", {}))
    default_assignee = routing_json.get("default", "")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    summary = f"Routing Config Update: {source_file}"

    description = (
        f"Routing configuration change applied.\n\n"
        f"Source file: {source_file}\n"
        f"Approved by: {approver}\n"
        f"Change summary: {service_count} service mapping(s), "
        f"{ou_count} OU mapping(s), default assignee: {default_assignee}\n"
        f"Timestamp: {timestamp}"
    )

    fields: dict = {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
        "summary": summary,
        "description": description,
    }

    if default_assignee:
        fields["assignee"] = {"accountId": default_assignee}

    return {"fields": fields}


def create_audit_ticket(
    jira_config: dict,
    routing_json: dict,
    source_file: str,
    approver: str,
) -> str | None:
    """Create a Jira ticket documenting the routing config change.

    Returns issue key on success, ``None`` on failure (logs warning, does
    not raise).
    """
    try:
        from aha_eventbridge_lambda.jira_client import JiraClient

        ticket_fields = build_audit_ticket_fields(
            routing_json=routing_json,
            source_file=source_file,
            approver=approver,
            project_key=jira_config["project_key"],
            issue_type=jira_config.get("issue_type", "Task"),
        )

        client = JiraClient.from_config(jira_config)
        result = client.create_issue(ticket_fields)
        issue_key = result.get("key", "")

        logger.info(json.dumps({
            "message": "Audit Jira ticket created",
            "issue_key": issue_key,
            "source_file": source_file,
            "approver": approver,
        }))

        return issue_key

    except Exception:
        logger.warning(json.dumps({
            "message": "Failed to create audit Jira ticket",
            "source_file": source_file,
            "approver": approver,
        }), exc_info=True)
        return None
