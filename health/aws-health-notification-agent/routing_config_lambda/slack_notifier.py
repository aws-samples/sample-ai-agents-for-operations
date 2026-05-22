# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Slack Workflow webhook notification for routing config review."""

from __future__ import annotations

import json
import logging
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ALLOWED_WEBHOOK_DOMAINS = {"hooks.slack.com"}


def _validate_webhook_url(url: str) -> None:
    """Validate webhook URL scheme and domain to prevent SSRF.

    Security: Prevents SSRF attacks by enforcing HTTPS, blocking internal AWS
    endpoints, and restricting to known Slack webhook domains.
    See security/SECURITY.md threat T-006 for the full threat analysis.
    """
    if not url.startswith("https://"):
        raise ValueError(f"Webhook URL must use HTTPS scheme, got: {url[:20]}")
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith(".amazonaws.com"):
        raise ValueError("Internal AWS endpoints not allowed as webhook targets")
    if parsed.hostname == "169.254.169.254":
        raise ValueError("Metadata endpoint not allowed as webhook target")
    if parsed.hostname not in ALLOWED_WEBHOOK_DOMAINS:
        raise ValueError(f"Webhook domain not in allowlist: {parsed.hostname}")


def build_workflow_payload(
    routing_json: dict,
    source_file: str,
    approval_url: str = "",
) -> dict:
    """Build the payload for a Slack Workflow webhook.

    Returns a dict with source_file, routing_json (pretty-printed),
    summary, and optionally approval_url.  Separated from the HTTP POST
    for testability (Property 3).
    """
    by_service: dict = routing_json.get("by_service", {})
    by_ou: dict = routing_json.get("by_ou", {})
    default: str = routing_json.get("default", "")

    payload: dict = {
        "source_file": source_file,
        "routing_json": json.dumps(routing_json, indent=2),
        "summary": (
            f"{len(by_service)} service mappings, "
            f"{len(by_ou)} OU mappings, "
            f"default: {default}"
        ),
        "approval_url": approval_url,
    }
    return payload


def post_routing_review(
    webhook_url: str,
    routing_json: dict,
    source_file: str,
    callback_id: str = "",
    approval_url: str = "",
) -> None:
    """Post routing config review to Slack via Workflow webhook.

    Posts source_file, pretty-printed routing_json, summary, and
    optionally approval_url to the Slack Workflow webhook URL.  The
    Workflow template in Slack controls the message formatting and
    whether to show an Approve button based on approval_url.

    Raises on HTTP failure after logging status and response body.
    """
    _validate_webhook_url(webhook_url)

    payload = build_workflow_payload(routing_json, source_file, approval_url)
    data = json.dumps(payload).encode()

    req = Request(
        webhook_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urlopen(req, timeout=30) as resp:  # nosec B310 # nosemgrep: dynamic-urllib-use-detected
            logger.info(json.dumps({
                "message": "Slack Workflow webhook POST succeeded",
                "status": resp.status,
                "source_file": source_file,
            }))
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        logger.warning(json.dumps({
            "message": "Slack Workflow webhook POST failed",
            "status": exc.code,
            "response_body": body,
            "source_file": source_file,
        }))
        raise


def post_health_event_notification(
    webhook_url: str,
    subject: str,
    summary: str,
    event_arn: str,
    approval_url: str = "",
) -> None:
    """Post a health event classification notification to Slack via Workflow webhook.

    Uses the same webhook as routing config but with health-event-specific fields.
    The Slack Workflow template maps source_file, routing_json, summary, approval_url.
    """
    _validate_webhook_url(webhook_url)

    payload = {
        "source_file": subject,
        "routing_json": summary,
        "summary": event_arn,
        "approval_url": approval_url,
    }
    data = json.dumps(payload).encode()

    req = Request(
        webhook_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urlopen(req, timeout=30) as resp:  # nosec B310 # nosemgrep: dynamic-urllib-use-detected
            logger.info(json.dumps({
                "message": "Health event Slack notification succeeded",
                "status": resp.status,
                "event_arn": event_arn,
            }))
    except HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        logger.warning(json.dumps({
            "message": "Health event Slack notification failed",
            "status": exc.code,
            "response_body": body,
            "event_arn": event_arn,
        }))
        raise
