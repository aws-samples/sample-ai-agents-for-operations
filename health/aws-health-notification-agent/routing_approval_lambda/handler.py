# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Routing Approval Lambda handler.

Handles two approval paths:
1. Slack interactive payloads (POST /slack/interactive)
2. Token-based approval via URL (GET/POST /approve-routing?token=...)

Both paths write approved routing config to Secrets Manager and
create an audit Jira ticket.
"""

import base64
import html as html_mod
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

import boto3

from routing_approval_lambda.audit_ticket import create_audit_ticket
from routing_approval_lambda.secrets_writer import update_routing_config
from routing_approval_lambda.slack_verifier import verify_slack_signature

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _handle_token_approval(event: dict) -> dict:
    """Handle GET/POST /approve-routing?token=... (Slack Workflow button path)."""
    qs = event.get("queryStringParameters") or {}
    token = qs.get("token", "")
    http_method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if not token:
        return {"statusCode": 400, "body": "Missing token parameter"}

    if not isinstance(token, str) or not re.match(r'^[a-zA-Z0-9_-]{36,64}$', token):
        return {"statusCode": 400, "body": "Invalid token format"}

    # Look up token in DynamoDB
    table_name = os.environ.get("APPROVAL_TABLE_NAME", "phd-approval-store")
    region = os.environ.get("AWS_REGION", "eu-west-1")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    resp = table.get_item(Key={"token": token})
    item = resp.get("Item")

    if not item:
        return {"statusCode": 404, "body": "Token not found or expired"}

    if item.get("status") == "executed":
        return {"statusCode": 200, "headers": {"Content-Type": "text/html"},
                "body": "<h2>Already Applied</h2><p>This routing config was already applied.</p>"}

    now = int(time.time())
    if int(item.get("expires_at", 0)) <= now:
        return {"statusCode": 410, "headers": {"Content-Type": "text/html"},
                "body": "<h2>Link Expired</h2><p>This approval link has expired.</p>"}

    source_file = item.get("source_file", "unknown")
    routing_json = json.loads(item["routing_json"])

    # GET = show confirmation page
    if http_method == "GET":
        pretty = html_mod.escape(json.dumps(routing_json, indent=2))
        safe_source = html_mod.escape(source_file)
        safe_token = html_mod.escape(token, quote=True)
        body_html = (
            f"<h2>Routing Config Approval</h2>"
            f"<p><b>Source:</b> {safe_source}</p>"
            f"<pre>{pretty}</pre>"
            f'<form method="POST" action="/approve-routing?token={safe_token}">'
            f'<button type="submit" style="background:#28a745;color:white;'
            f'padding:10px 20px;border:none;border-radius:4px;cursor:pointer;">'
            f'Confirm &amp; Apply</button></form>'
        )
        return {"statusCode": 200, "headers": {"Content-Type": "text/html"}, "body": body_html}

    # POST = execute approval
    secret_arn = os.environ["JIRA_SECRET_ARN"]
    try:
        update_routing_config(secret_arn, routing_json)
    except Exception:
        logger.error(json.dumps({
            "message": "Failed to update routing config",
            "token": token,
            "source_file": source_file,
        }), exc_info=True)
        return {"statusCode": 500, "body": "Failed to apply routing config. Check logs."}

    # Mark token as executed
    table.update_item(
        Key={"token": token},
        UpdateExpression="SET #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "executed"},
    )

    # Audit Jira ticket (best-effort)
    jira_config = {
        "base_url": os.environ.get("JIRA_BASE_URL", ""),
        "project_key": os.environ.get("JIRA_PROJECT_KEY", ""),
        "issue_type": os.environ.get("JIRA_ISSUE_TYPE", "Task"),
        "user_email": os.environ.get("JIRA_USER_EMAIL", ""),
        "secret_arn": secret_arn,
    }
    create_audit_ticket(jira_config, routing_json, source_file, "slack-approval")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.info(json.dumps({
        "message": "Routing config applied via token approval",
        "token": token,
        "source_file": source_file,
        "timestamp": ts,
    }))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html"},
        "body": f"<h2>✅ Routing Config Applied</h2><p>Updated at {ts}</p>",
    }


def _handle_slack_interactive(event: dict) -> dict:
    """Handle POST /slack/interactive (Slack app interactive payload)."""
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    headers = event.get("headers", {})
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")

    # Fail closed: reject requests if signing secret is not configured
    if not signing_secret:
        logger.error(json.dumps({"message": "SLACK_SIGNING_SECRET not configured — rejecting request"}))
        return {"statusCode": 500, "body": json.dumps({"text": "Server misconfiguration"})}

    if not verify_slack_signature(signing_secret, timestamp, body, signature):
        source_ip = event.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")
        logger.warning(json.dumps({"message": "Slack signature verification failed", "source_ip": source_ip}))
        return {"statusCode": 401, "body": json.dumps({"text": "Unauthorized"})}

    payload = json.loads(parse_qs(body)["payload"][0])
    action_name = payload["actions"][0]["name"]
    user_name = payload["user"]["name"]

    if action_name == "reject":
        logger.info(json.dumps({"message": "Routing config change rejected", "user": user_name}))
        return {"statusCode": 200, "body": json.dumps({"text": f"Routing config change rejected by {user_name}."})}

    if action_name == "approve":
        routing_json = json.loads(payload["actions"][0]["value"])

        # Validate routing JSON schema before writing to Secrets Manager
        from routing_config_lambda.bedrock_invoker import validate_routing_json
        if not validate_routing_json(routing_json):
            logger.warning(json.dumps({"message": "Invalid routing JSON structure from Slack payload"}))
            return {"statusCode": 400, "body": json.dumps({"text": "Invalid routing configuration format"})}

        source_file = payload["callback_id"].replace("routing-config:", "")
        secret_arn = os.environ["JIRA_SECRET_ARN"]

        try:
            update_routing_config(secret_arn, routing_json)
        except Exception:
            logger.error(json.dumps({"message": "Failed to update routing config", "source_file": source_file, "user": user_name}), exc_info=True)
            return {"statusCode": 200, "body": json.dumps({"text": "❌ Failed to update routing config. Check logs."})}

        jira_config = {
            "base_url": os.environ.get("JIRA_BASE_URL", ""),
            "project_key": os.environ.get("JIRA_PROJECT_KEY", ""),
            "issue_type": os.environ.get("JIRA_ISSUE_TYPE", "Task"),
            "user_email": os.environ.get("JIRA_USER_EMAIL", ""),
            "secret_arn": secret_arn,
        }
        create_audit_ticket(jira_config, routing_json, source_file, user_name)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        logger.info(json.dumps({"message": "Routing config updated", "source_file": source_file, "user": user_name, "timestamp": ts}))
        return {"statusCode": 200, "body": json.dumps({"text": f"✅ Routing config updated by {user_name} at {ts}."})}

    return {"statusCode": 200, "body": json.dumps({"text": f"Unknown action: {action_name}"})}


def handler(event: dict, context) -> dict:
    """Route to the appropriate handler based on the request path."""
    path = event.get("rawPath", "") or event.get("requestContext", {}).get("http", {}).get("path", "")

    if "/approve-routing" in path:
        return _handle_token_approval(event)
    else:
        return _handle_slack_interactive(event)
