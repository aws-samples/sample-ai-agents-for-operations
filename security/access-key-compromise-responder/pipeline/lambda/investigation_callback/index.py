# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Investigation Callback Lambda.

Calls the AWS DevOps Agent webhook to start an investigation, passing the
normalized incident. The webhook is HMAC-signed with a secret stored in AWS
Secrets Manager.

This function does not mutate customer resources.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WEBHOOK_URL = os.environ.get("DEVOPS_AGENT_WEBHOOK_URL", "")
WEBHOOK_SECRET_ARN = os.environ.get("WEBHOOK_SECRET_ARN", "")

_secrets = boto3.client("secretsmanager")
_secret_cache = {}


def lambda_handler(event, context):
    logger.info("Callback lambda input: %s", json.dumps(event, default=str)[:2000])
    return _invoke_agent(event)


def _get_secret():
    if not WEBHOOK_SECRET_ARN:
        return ""
    if "value" not in _secret_cache:
        resp = _secrets.get_secret_value(SecretId=WEBHOOK_SECRET_ARN)
        _secret_cache["value"] = resp.get("SecretString", "")
    return _secret_cache["value"]


def _invoke_agent(event):
    incident = event.get("incident", {})

    if not WEBHOOK_URL:
        logger.warning("DEVOPS_AGENT_WEBHOOK_URL not set; skipping real invocation.")
        return {"invoked": False, "reason": "webhook_url_not_configured"}

    # Map our detection severity hint to the webhook priority enum.
    severity_hint = (incident.get("severity_hint") or "HIGH").upper()
    priority = severity_hint if severity_hint in (
        "CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL"
    ) else "HIGH"

    key = incident.get("access_key_id") or "unknown-key"
    account = incident.get("account_id") or "unknown-account"
    # Unique incidentId + timestamp so the webhook does not deduplicate us.
    incident_id = f"akc-{key}-{int(time.time())}"

    # Body must match the DevOps Agent webhook incident schema. Our normalized
    # incident rides in `data`.
    payload_obj = {
        "eventType": "incident",
        "incidentId": incident_id,
        "action": "created",
        "priority": priority,
        "title": f"Compromised AWS access key {key} in account {account}",
        "description": (
            "Investigate the potentially compromised AWS access key using the "
            "access-key-compromise-investigation skill. Return findings in the "
            "skill's fixed Markdown output contract. This is an advisory, "
            "read-only investigation; recommend human-run containment only."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "AccessKeyCompromiseResponder",
        "data": {
            "incident": incident,
        },
    }
    payload = json.dumps(payload_obj)
    body = payload.encode("utf-8")

    # HMAC-SHA256 over "<timestamp>:<payload>", base64-encoded, per the
    # DevOps Agent webhook spec. Timestamp header must match the signed value.
    ts = payload_obj["timestamp"]
    headers = {
        "Content-Type": "application/json",
        "x-amzn-event-timestamp": ts,
    }
    secret = _get_secret()
    if secret:
        digest = hmac.new(
            secret.encode("utf-8"), f"{ts}:{payload}".encode("utf-8"), hashlib.sha256
        ).digest()
        headers["x-amzn-event-signature"] = base64.b64encode(digest).decode("utf-8")

    # Only allow HTTPS webhook endpoints. This prevents a misconfigured or
    # tampered URL from using urllib's file:///other schemes to read local
    # resources (semgrep: dynamic-urllib-use-detected).
    if not WEBHOOK_URL.lower().startswith("https://"):
        raise ValueError("DEVOPS_AGENT_WEBHOOK_URL must be an https:// URL")

    req = urllib.request.Request(WEBHOOK_URL, data=body, headers=headers, method="POST")
    try:
        # URL comes from a stack parameter (operator-configured), not user
        # input, and is validated to be https above; not attacker-controllable.
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa # nosec B310 # nosemgrep
            status = resp.status
            resp_body = resp.read().decode("utf-8", "replace")
        logger.info("Webhook accepted investigation (status %s): %s", status, resp_body[:300])
        return {"invoked": True, "status": status, "incident_id": incident_id}
    except urllib.error.HTTPError as e:
        # The webhook responded with a non-2xx status (e.g. 4xx/5xx). Include
        # the status code and any response body so the operator can diagnose a
        # misconfigured Agent Space webhook or a bad HMAC signature.
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # nosec B110 - best-effort body read for diagnostics
            pass
        logger.warning(
            "DevOps Agent webhook returned HTTP %s for incident %s: %s",
            e.code, incident_id, detail,
        )
        raise RuntimeError(
            f"DevOps Agent webhook returned HTTP {e.code} for incident "
            f"{incident_id}: {detail}"
        ) from e
    except urllib.error.URLError as e:
        # The webhook was unreachable (DNS failure, connection refused,
        # timeout, TLS error). Re-raise with context so the Step Functions
        # workflow's failure routing (DLQ) records a meaningful cause.
        logger.warning(
            "DevOps Agent webhook unreachable for incident %s: %s",
            incident_id, e.reason,
        )
        raise RuntimeError(
            f"DevOps Agent webhook unreachable for incident {incident_id}: "
            f"{e.reason}"
        ) from e
