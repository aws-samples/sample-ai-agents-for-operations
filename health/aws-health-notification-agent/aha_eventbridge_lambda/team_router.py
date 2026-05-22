# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Team routing — resolve Jira assignee from multi-level mappings."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_assignee(
    affected_service: str,
    affected_accounts: list[dict],
    service_team_map: dict[str, str],
    ou_team_map: dict[str, str],
    default_assignee: str,
    resource_team_map: dict[str, str] | None = None,
    account_team_map: dict[str, str] | None = None,
    affected_entities: list[str] | None = None,
) -> str:
    """Determine Jira assignee from multi-level routing mappings.

    Priority chain (highest to lowest):
    1. Resource name match in resource_team_map (cluster names, instance IDs)
    2. Account ID match in account_team_map
    3. Service name match in service_team_map
    4. OU match from affected accounts in ou_team_map
    5. default_assignee

    Args:
        affected_service: AWS service name (e.g., "EKS", "RDS").
        affected_accounts: List of account dicts with optional "ou", "ou_path", "account_id" keys.
        service_team_map: Maps service names to team names.
        ou_team_map: Maps OU names/paths to team names.
        default_assignee: Fallback assignee if no mapping matches.
        resource_team_map: Maps resource names (e.g., cluster names) to team names.
        account_team_map: Maps AWS account IDs to team names.
        affected_entities: List of resource identifiers (e.g., cluster names) from the event.

    Returns:
        Team name or Jira assignee account ID string.
    """
    resource_team_map = resource_team_map or {}
    account_team_map = account_team_map or {}
    affected_entities = affected_entities or []

    # 1. Resource match (highest priority)
    for entity in affected_entities:
        if entity in resource_team_map:
            return resource_team_map[entity]
        # Try matching just the resource name from ARN
        if "/" in entity:
            name = entity.rsplit("/", 1)[-1]
            if name in resource_team_map:
                return resource_team_map[name]

    # 2. Account match
    for account in affected_accounts:
        account_id = account.get("account_id") or account.get("awsAccountId") or ""
        if account_id and account_id in account_team_map:
            return account_team_map[account_id]

    # 3. Service match
    if affected_service in service_team_map:
        return service_team_map[affected_service]
    # Case-insensitive fallback
    for svc, assignee in service_team_map.items():
        if svc.upper() == affected_service.upper():
            return assignee

    # 4. OU match from affected accounts
    for account in affected_accounts:
        ou = account.get("ou") or account.get("ou_path") or ""
        if ou and ou in ou_team_map:
            return ou_team_map[ou]

    # 5. Default
    return default_assignee
