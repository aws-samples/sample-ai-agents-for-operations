# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Notifier Lambda.

Delivers the investigation report and human-run recommendations to the right
people. Routing preference:

  1. If Slack / Microsoft Teams webhook URLs are configured (in Secrets
     Manager), post to them.
  2. Always publish to the default SNS topic (email subscription) as the
     reliable fallback so a fresh deployment still reaches someone.

It also includes the account alternate contacts (resolved by the Event Router)
in the message body so the recipient knows who owns the account.

This function sends notifications only. It does not mutate customer resources.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
CHANNELS_SECRET_ARN = os.environ.get("CHANNELS_SECRET_ARN", "")

sns = boto3.client("sns")
_secrets = boto3.client("secretsmanager")
_cache = {}


def lambda_handler(event, context):
    logger.info("Notifier input: %s", json.dumps(event, default=str)[:2000])

    incident = event.get("incident", {})

    # The pipeline only sends the "triggered" heads-up notification (an
    # investigation was started). Findings are reviewed in the DevOps Agent
    # operator app.
    subject, body = _triggered_message(incident, event.get("invocation", {}))
    severity = incident.get("severity_hint", "HIGH")

    channels = _load_channels()
    delivered = []

    if channels.get("slackWebhookUrl"):
        try:
            _post_slack(channels["slackWebhookUrl"], subject, body)
            delivered.append("slack")
        except Exception as e:
            # _post_json raises RuntimeError with the endpoint host + status.
            logger.error("Slack delivery failed, falling back to SNS: %s", e)

    if channels.get("msTeamsWebhookUrl"):
        try:
            _post_teams(channels["msTeamsWebhookUrl"], subject, body)
            delivered.append("teams")
        except Exception as e:
            # _post_json raises RuntimeError with the endpoint host + status.
            logger.error("Teams delivery failed, falling back to SNS: %s", e)

    # Default SNS fallback — always attempt.
    if SNS_TOPIC_ARN:
        try:
            sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject[:100], Message=body)
            delivered.append("sns")
        except Exception as e:
            logger.error("SNS publish failed: %s", e)

    logger.info("Delivered via: %s", delivered)
    return {"delivered": delivered, "severity": severity, "mode": "triggered"}


def _triggered_message(incident, invocation):
    """Heads-up notification: an investigation was triggered (Path A)."""
    ctx = incident.get("account_context", {})
    acct = incident.get("account_id", "unknown")
    key = incident.get("access_key_id", "unknown-key")
    subject = f"[Investigation triggered] Access key {key} in {acct}"
    contacts = ctx.get("alternate_contacts", {})
    contact_lines = [
        f"  - {t}: {c.get('name')} <{c.get('email')}>"
        for t, c in contacts.items() if c and c.get("email")
    ]
    contacts_block = "\n".join(contact_lines) or "  - (none set)"
    body = (
        f"AWS DevOps Agent — access key compromise investigation triggered\n"
        f"{'=' * 60}\n\n"
        f"Account: {acct} ({ctx.get('account_name') or 'name unknown'})\n"
        f"Account owner email: {ctx.get('account_email') or 'unknown'}\n"
        f"Alternate contacts:\n{contacts_block}\n"
        f"Account tags: {json.dumps(ctx.get('account_tags', {}))}\n\n"
        f"Access key: {key}\n"
        f"Principal: {incident.get('username') or 'unresolved'}\n"
        f"Detection source: {incident.get('alert_source')}\n"
        f"Detected at: {incident.get('detected_at')}\n"
        f"Webhook invocation: {json.dumps(invocation)[:300]}\n\n"
        f"An investigation has been started in AWS DevOps Agent using the "
        f"access-key-compromise-investigation skill. Review the findings and "
        f"the human-run containment recommendations in the DevOps Agent "
        f"operator app. This system does not take any action automatically.\n"
    )
    return subject, body


def _load_channels():
    if not CHANNELS_SECRET_ARN:
        return {}
    if "channels" not in _cache:
        try:
            resp = _secrets.get_secret_value(SecretId=CHANNELS_SECRET_ARN)
            _cache["channels"] = json.loads(resp.get("SecretString", "{}"))
        except Exception as e:
            # No channel config found (Slack/Teams not configured); fall back to SNS.
            logger.info("Channel config unavailable: %s", e)
            _cache["channels"] = {}
    return _cache["channels"]


def _post_slack(url, subject, body):
    payload = {
        "text": subject,
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": subject[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"```{body[:2900]}```"}},
        ],
    }
    _post_json(url, payload)


def _post_teams(url, subject, body):
    payload = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "summary": subject[:150],
        "title": subject[:150],
        "text": f"<pre>{body[:16000]}</pre>",
    }
    _post_json(url, payload)


def _post_json(url, payload):
    # Only allow HTTPS chat webhook endpoints (semgrep: dynamic-urllib-use-detected).
    if not str(url).lower().startswith("https://"):
        raise ValueError("Notification webhook URL must be an https:// URL")
    # Host only (no path/query) so we never log a secret-bearing webhook token.
    endpoint = urlsplit(url).hostname or "unknown-host"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    # URL is an operator-configured Slack/Teams webhook (from Secrets Manager),
    # not user input, and is validated to be https above; not attacker-controllable.
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa # nosec B310 # nosemgrep
            return resp.status
    except urllib.error.HTTPError as e:
        # Non-2xx from the chat service (e.g. 404 for a revoked webhook, 429
        # rate limit). Surface host + status; the caller logs and falls back
        # to SNS.
        raise RuntimeError(
            f"Webhook POST to {endpoint} failed with HTTP {e.code}"
        ) from e
    except urllib.error.URLError as e:
        # Unreachable endpoint (DNS/connection/timeout/TLS). Surface host +
        # reason.
        raise RuntimeError(
            f"Webhook POST to {endpoint} failed: {e.reason}"
        ) from e
