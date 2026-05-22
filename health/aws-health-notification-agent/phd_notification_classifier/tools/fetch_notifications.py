# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

from strands import tool
import boto3


@tool
def fetch_phd_notifications(limit: int = 0) -> list:
    """Fetch unclosed PHD notifications from the organization.

    Retrieves open and upcoming health events using the AWS Health API
    (describe_events_for_organization), handles pagination, fetches event
    descriptions, and fetches affected accounts per event.

    Args:
        limit: Maximum number of notifications to return. 0 (default) means
            no limit — return all notifications. Use a positive integer to
            cap the result set and avoid context window overflow with large
            notification volumes.

    Returns:
        List of dicts, each containing: arn, service, eventTypeCode,
        eventTypeCategory, statusCode, region, eventDescription, and
        affectedAccounts.
    """
    client = boto3.client("health", region_name="us-east-1")
    events = []
    paginator = client.get_paginator("describe_events_for_organization")
    page_iterator = paginator.paginate(
        filter={"eventStatusCodes": ["open", "upcoming"]}
    )
    for page in page_iterator:
        events.extend(page.get("events", []))

    # Apply limit before expensive per-event API calls
    if limit > 0:
        events = events[:limit]

    # Fetch descriptions and affected accounts for all events
    for event in events:
        try:
            details = client.describe_event_details_for_organization(
                organizationEventDetailFilters=[
                    {"eventArn": event["arn"]}
                ]
            )
            successful = details.get("successfulSet", [])
            if successful:
                event["eventDescription"] = (
                    successful[0]
                    .get("eventDescription", {})
                    .get("latestDescription", "")
                )
        except Exception:
            event["eventDescription"] = ""

        try:
            accounts_response = client.describe_affected_accounts_for_organization(
                eventArn=event["arn"]
            )
            event["affectedAccounts"] = accounts_response.get("affectedAccounts", [])
        except Exception:
            event["affectedAccounts"] = []

    return [
        {
            "arn": e.get("arn", ""),
            "service": e.get("service", ""),
            "eventTypeCode": e.get("eventTypeCode", ""),
            "eventTypeCategory": e.get("eventTypeCategory", ""),
            "statusCode": e.get("statusCode", ""),
            "region": e.get("region", ""),
            "eventDescription": e.get("eventDescription", ""),
            "affectedAccounts": e.get("affectedAccounts", []),
        }
        for e in events
    ]
