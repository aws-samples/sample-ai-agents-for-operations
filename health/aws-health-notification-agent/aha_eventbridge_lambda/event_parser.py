# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Parse AWS Health events delivered via EventBridge.

Supports two event formats:
1. Raw AWS Health events (source: aws.health, detail-type: AWS Health Event)
   - detail is a dict with eventDescription as a list of dicts
2. AHA-forwarded events (source: aha, detail-type: AHA Event)
   - Detail is a JSON string that needs json.loads()
   - eventDescription is a dict with latestDescription (not a list)
   - affectedEntities includes awsAccountName, entityUrl, entityMetadata
"""

import json


def parse_health_event(event: dict) -> dict:
    """Extract fields from an EventBridge health event payload.

    Handles both raw AWS Health events and AHA-forwarded events.

    Args:
        event: Raw EventBridge event dict.

    Returns:
        Dict with keys: event_arn, status_code, affected_accounts,
        event_type_category, event_description, service,
        affected_entities (full entity details).

    Raises:
        ValueError: If eventArn or eventTypeCategory is missing from detail.
    """
    detail = event.get("detail", event.get("Detail", {}))

    # AHA events have Detail as a JSON string
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            raise ValueError(f"Failed to parse Detail as JSON: {detail[:200]}")

    event_arn = detail.get("eventArn")
    event_type_category = detail.get("eventTypeCategory")

    if not event_arn:
        raise ValueError("Health event is missing required field: eventArn")
    if not event_type_category:
        raise ValueError("Health event is missing required field: eventTypeCategory")

    # Extract affected entities with full metadata
    affected_entities = detail.get("affectedEntities", [])
    affected_accounts = list(
        {entity.get("awsAccountId") for entity in affected_entities if entity.get("awsAccountId")}
    )

    # Extract description — handle both formats:
    # Raw AWS Health: eventDescription is a list of dicts with latestDescription
    # AHA format: eventDescription is a dict with latestDescription
    event_desc_raw = detail.get("eventDescription", "")
    event_description = ""
    if isinstance(event_desc_raw, list) and event_desc_raw:
        event_description = event_desc_raw[0].get("latestDescription", "")
    elif isinstance(event_desc_raw, dict):
        event_description = event_desc_raw.get("latestDescription", "")
    elif isinstance(event_desc_raw, str):
        event_description = event_desc_raw

    return {
        "event_arn": event_arn,
        "status_code": detail.get("statusCode", ""),
        "affected_accounts": affected_accounts,
        "event_type_category": event_type_category,
        "event_type_code": detail.get("eventTypeCode", ""),
        "event_description": event_description,
        "service": detail.get("service", ""),
        "affected_entities": affected_entities,
    }
