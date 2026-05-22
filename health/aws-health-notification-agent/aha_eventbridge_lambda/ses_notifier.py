# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Amazon SES email notifier for human-approval remediation workflow."""

import os
import logging

import boto3

logger = logging.getLogger(__name__)


def _extract_region_from_arn(arn: str) -> str | None:
    """Extract AWS region from a SES identity ARN."""
    try:
        parts = arn.split(":")
        if len(parts) >= 4 and parts[0] == "arn":
            return parts[3] or None
    except Exception:
        logger.debug("Failed to extract region from ARN: %s", arn)
    return None


def build_html_email(plain_text_body: str, approval_actions: list[dict]) -> str:
    """Build HTML email body with styled approval buttons.

    Args:
        plain_text_body: Plain-text summary (existing format_summary output).
        approval_actions: List of dicts with keys:
            - description: Human-readable action description
            - approval_url: Full HTTPS URL with token
            - expires_at: ISO 8601 expiry timestamp

    Returns:
        HTML string with summary and approval buttons.
    """
    actions_html = ""
    for action in approval_actions:
        actions_html += (
            '<div style="margin:16px 0;padding:16px;border:1px solid #ddd;border-radius:4px;">'
            f'<p style="margin:0 0 8px 0;font-weight:bold;">{action["description"]}</p>'
            f'<a href="{action["approval_url"]}" style="display:inline-block;padding:10px 24px;'
            "background-color:#28a745;color:#ffffff;text-decoration:none;border-radius:4px;"
            'font-weight:bold;font-size:14px;">Approve</a>'
            f'<p style="margin:8px 0 0 0;font-size:12px;color:#666;">Expires: {action["expires_at"]}</p>'
            "</div>"
        )

    html = (
        "<html><body>"
        '<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">'
        "<h2>AWS Health Event Summary</h2>"
        f"<pre>{plain_text_body}</pre>"
        "<h3>Remediation Actions</h3>"
        f"{actions_html}"
        "</div>"
        "</body></html>"
    )
    return html


def send_approval_email(
    recipient: str,
    sender: str,
    subject: str,
    plain_text_body: str,
    approval_actions: list[dict],
    notification_context: dict,
) -> dict:
    """Send an HTML email with clickable approval buttons and plain-text fallback.

    Args:
        recipient: Destination email address.
        sender: Verified SES sender identity (email or ARN).
        subject: Email subject line.
        plain_text_body: Plain-text summary (existing format_summary output).
        approval_actions: List of dicts with keys:
            - description: Human-readable action description
            - approval_url: Full HTTPS URL with token
            - expires_at: ISO 8601 expiry timestamp
        notification_context: Event context for the email body.

    Returns:
        SES SendEmail response dict.
    """
    region = _extract_region_from_arn(sender) or os.environ.get("AWS_REGION", "eu-west-1")
    # Required IAM permissions (defined in aha_eventbridge_lambda/template.yaml):
    # {
    #   "Effect": "Allow",
    #   "Action": ["ses:SendEmail", "ses:SendRawEmail"],
    #   "Resource": "arn:aws:ses:${Region}:${AccountId}:identity/${SesIdentityArn}"
    # }
    # Scoped to the specific verified sender identity — prevents use of other identities.
    ses = boto3.client("ses", region_name=region)

    html_body = build_html_email(plain_text_body, approval_actions)

    # Build plain-text fallback with raw approval URLs appended
    text_body = plain_text_body + "\n\n--- Approval Links ---\n"
    for action in approval_actions:
        text_body += f"\n{action['description']}\n  {action['approval_url']}\n  Expires: {action['expires_at']}\n"

    response = ses.send_email(
        Source=sender,
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text_body, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        },
    )
    return response


def send_confirmation_email(
    recipient: str,
    sender: str,
    subject: str,
    status: str,
    actions_taken: list[str],
    error: str | None,
    notification_context: dict,
) -> dict:
    """Send a confirmation email after remediation execution.

    Args:
        recipient: Destination email address.
        sender: Verified SES sender identity (email or ARN).
        subject: Email subject line.
        status: Execution status ("success", "error", or "failed").
        actions_taken: List of executed action descriptions.
        error: Error message if status is not success.
        notification_context: Original event context (event_arn, affected_service, affected_accounts).

    Returns:
        SES SendEmail response dict.
    """
    region = _extract_region_from_arn(sender) or os.environ.get("AWS_REGION", "eu-west-1")
    ses = boto3.client("ses", region_name=region)

    event_arn = notification_context.get("event_arn", "N/A")
    service = notification_context.get("affected_service", "N/A")
    accounts = notification_context.get("affected_accounts", [])
    accounts_str = ", ".join(
        a.get("account_id", "unknown") if isinstance(a, dict) else str(a)
        for a in accounts
    ) or "N/A"

    context_text = (
        f"Event ARN: {event_arn}\n"
        f"Affected Service: {service}\n"
        f"Affected Accounts: {accounts_str}"
    )

    if status == "success":
        actions_list = "\n".join(f"  - {a}" for a in actions_taken) if actions_taken else "  (none)"
        body_text = f"Remediation completed successfully.\n\nActions taken:\n{actions_list}\n\n{context_text}"
        status_label = "SUCCESS"
        status_color = "#28a745"
    else:
        body_text = f"Remediation failed.\n\nError: {error or 'Unknown error'}\n\n{context_text}"
        status_label = "FAILED"
        status_color = "#dc3545"

    html_body = (
        "<html><body>"
        '<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">'
        f'<h2 style="color:{status_color};">Remediation {status_label}</h2>'
        f"<pre>{body_text}</pre>"
        "</div>"
        "</body></html>"
    )

    response = ses.send_email(
        Source=sender,
        Destination={"ToAddresses": [recipient]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": body_text, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        },
    )
    return response
