# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Approval Lambda — handles GET /approve?token=xxx requests."""

import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

logger = logging.getLogger(__name__)

TABLE_NAME = os.environ.get("APPROVAL_TABLE_NAME", "phd-approval-store")
AGENT_ENDPOINT = os.environ.get("AGENT_RUNTIME_ENDPOINT_ARN", "")
SES_SENDER = os.environ.get("SES_SENDER_IDENTITY", "")
RECIPIENT = os.environ.get("NOTIFICATION_RECIPIENT_EMAIL", "")
REGION = os.environ.get("AWS_REGION", "eu-west-1")


class TokenExpiredError(Exception):
    pass


class TokenAlreadyUsedError(Exception):
    pass


class TokenNotFoundError(Exception):
    pass


def _html_response(status_code, title, message):
    """Return an HTML response page."""
    colors = {200: "#28a745", 404: "#6c757d", 409: "#ffc107", 410: "#dc3545", 500: "#dc3545"}
    color = colors.get(status_code, "#6c757d")
    body = (
        f"<html><body><div style='font-family:Arial,sans-serif;max-width:500px;margin:40px auto;text-align:center;'>"
        f"<h2 style='color:{color};'>{title}</h2><p>{message}</p></div></body></html>"
    )
    return {"statusCode": status_code, "headers": {"Content-Type": "text/html"}, "body": body}


def _validate_and_approve_token(token):
    """Atomically validate and approve a token using DynamoDB conditional update.

    Uses ConditionExpression: attribute_exists(token) AND #s = :pending AND expires_at > :now

    Returns:
        The approval record on success.

    Raises:
        TokenExpiredError: Token has passed its expiry.
        TokenAlreadyUsedError: Token status is not pending.
        TokenNotFoundError: Token does not exist.
    """
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)
    now = int(time.time())
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        response = table.update_item(
            Key={"token": token},
            UpdateExpression="SET #s = :approved, approved_at = :now_iso",
            ConditionExpression=(
                Attr("token").exists()
                & Attr("status").eq("pending")
                & Attr("expires_at").gt(now)
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":approved": "approved", ":now_iso": now_iso},
            ReturnValues="ALL_NEW",
        )
        return response["Attributes"]
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        # Determine why the conditional check failed
        try:
            item = table.get_item(Key={"token": token}).get("Item")
        except Exception:
            raise TokenNotFoundError(f"Token not found: {token[:8]}...")
        if not item:
            raise TokenNotFoundError(f"Token not found: {token[:8]}...")
        if item.get("status") != "pending":
            raise TokenAlreadyUsedError(f"Token already {item.get('status')}: {token[:8]}...")
        if int(item.get("expires_at", 0)) <= now:
            raise TokenExpiredError(f"Token expired: {token[:8]}...")
        raise TokenNotFoundError(f"Token validation failed: {token[:8]}...")


MAX_RETRIES = 3
BASE_DELAY_SECONDS = 1

_TRANSIENT_ERROR_CODES = {"ThrottlingException", "TooManyRequestsException"}


def _is_transient_error(exc: Exception) -> bool:
    """Return True if the exception is eligible for retry."""
    from botocore.exceptions import ClientError

    if isinstance(exc, ClientError):
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in _TRANSIENT_ERROR_CODES:
            return True
        http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return http_status >= 500

    if isinstance(exc, (ConnectionError, TimeoutError, RuntimeError)):
        return True

    return False


def _invoke_remediation(endpoint_arn, remediation_payload):
    """Invoke AgentCore with a remediation_action payload.

    Retries transient errors up to 3 times with exponential backoff.

    Args:
        endpoint_arn: AgentCore Runtime endpoint ARN.
        remediation_payload: The remediation actions to execute.

    Returns:
        Dict with status, actions_taken, and optional error.
    """
    import uuid
    from botocore.config import Config
    from aha_eventbridge_lambda.response_parser import (
        read_streaming_response,
        unwrap_response,
        extract_json_from_text,
    )

    client = boto3.client(
        "bedrock-agentcore",
        region_name=REGION,
        config=Config(read_timeout=300, connect_timeout=10),
    )
    payload = json.dumps({"remediation_action": remediation_payload})
    session_id = f"remediation-{uuid.uuid4()}"

    logger.info(json.dumps({"message": "Invoking remediation", "endpoint": endpoint_arn, "session_id": session_id}))

    last_exception = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.invoke_agent_runtime(
                agentRuntimeArn=endpoint_arn,
                runtimeSessionId=session_id,
                payload=payload.encode("utf-8"),
            )

            result_text = read_streaming_response(response, session_id)
            text = unwrap_response(result_text)

            try:
                return extract_json_from_text(text)
            except ValueError:
                return {"status": "success", "actions_taken": [str(text)[:500]], "error": None}
        except Exception as exc:
            last_exception = exc

            if not _is_transient_error(exc):
                raise

            if attempt < MAX_RETRIES:
                delay = BASE_DELAY_SECONDS * (2 ** attempt)
                logger.warning(json.dumps({
                    "message": "Transient error during remediation, retrying",
                    "session_id": session_id,
                    "attempt": attempt + 1,
                    "max_retries": MAX_RETRIES,
                    "delay_seconds": delay,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }))
                time.sleep(delay)

    raise last_exception


def _update_status(token, status, execution_result=None):
    """Update the approval record status in DynamoDB.

    Args:
        token: The approval token (partition key).
        status: New status value (e.g., 'executed', 'failed').
        execution_result: Optional dict with execution details to store.
    """
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)
    now_iso = datetime.now(timezone.utc).isoformat()

    update_expr = "SET #s = :status, executed_at = :now"
    expr_values = {":status": status, ":now": now_iso}

    if execution_result:
        update_expr += ", execution_result = :result"
        expr_values[":result"] = json.loads(json.dumps(execution_result, default=str))

    table.update_item(
        Key={"token": token},
        UpdateExpression=update_expr,
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues=expr_values,
    )


def handler(event, context):
    """Handle approval requests with two-step confirmation.

    GET /approve?token=xxx  → Show confirmation page (safe for email link pre-fetch)
    POST /approve           → Execute the remediation after user clicks Confirm
    """
    http_method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    params = event.get("queryStringParameters") or {}
    source_ip = event.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")

    if http_method == "GET":
        return _handle_get(params, source_ip)
    elif http_method == "POST":
        return _handle_post(event, source_ip)
    else:
        return _html_response(405, "Method Not Allowed", "Use GET or POST.")


def _handle_get(params, source_ip):
    """Show a confirmation page — does NOT execute remediation."""
    token = params.get("token")
    if not token:
        return _html_response(400, "Bad Request", "Missing 'token' query parameter.")
    if not isinstance(token, str) or not re.match(r'^[a-zA-Z0-9_-]{36,64}$', token):
        return _html_response(400, "Bad Request", "Invalid token format.")

    token_prefix = token[:8]
    logger.info(json.dumps({"message": "Confirmation page viewed", "token_prefix": token_prefix, "source_ip": source_ip}))

    # Check token validity without changing status
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)
    try:
        resp = table.get_item(Key={"token": token})
        item = resp.get("Item")
    except Exception:
        return _html_response(500, "Error", "An error occurred.")

    if not item:
        return _html_response(404, "Not Found", "Approval token not found.")

    status = item.get("status", "")
    if status != "pending":
        return _html_response(409, "Already Processed", f"This action has already been processed (status: {status}).")

    now = int(time.time())
    if int(item.get("expires_at", 0)) <= now:
        return _html_response(410, "Link Expired", "This approval link has expired.")

    # Show confirmation page with a POST form
    description = item.get("remediation_payload", {}).get("action_type", "remediation action")
    service = item.get("notification_context", {}).get("affected_service", "Unknown")
    expires = item.get("expires_at_iso", "N/A")

    body = (
        "<html><body>"
        "<div style='font-family:Arial,sans-serif;max-width:500px;margin:40px auto;text-align:center;'>"
        "<h2>Confirm Remediation</h2>"
        f"<p>You are about to approve: <strong>{html.escape(service)} — {html.escape(description)}</strong></p>"
        f"<p style='font-size:12px;color:#666;'>Expires: {html.escape(str(expires))}</p>"
        f"<form method='POST' action='/prod/approve'>"
        f"<input type='hidden' name='token' value='{html.escape(token, quote=True)}'/>"
        "<button type='submit' style='padding:12px 32px;background-color:#28a745;"
        "color:#fff;border:none;border-radius:4px;font-size:16px;cursor:pointer;'>"
        "Confirm &amp; Execute</button>"
        "</form>"
        "<p style='margin-top:16px;font-size:12px;color:#999;'>Click the button above to execute the remediation. "
        "This action cannot be undone.</p>"
        "</div></body></html>"
    )
    return {"statusCode": 200, "headers": {"Content-Type": "text/html"}, "body": body}


def _handle_post(event, source_ip):
    """Execute remediation after user confirms via POST."""
    # Parse token from POST body (form-encoded)
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")

    # Parse form data: token=xxx
    from urllib.parse import parse_qs
    form_data = parse_qs(body)
    token = form_data.get("token", [None])[0]

    if not token:
        return _html_response(400, "Bad Request", "Missing token.")
    if not isinstance(token, str) or not re.match(r'^[a-zA-Z0-9_-]{36,64}$', token):
        return _html_response(400, "Bad Request", "Invalid token format.")

    token_prefix = token[:8]
    logger.info(json.dumps({"message": "Approval confirmed via POST", "token_prefix": token_prefix, "source_ip": source_ip}))

    try:
        record = _validate_and_approve_token(token)
        logger.info(json.dumps({"message": "Token approved", "token_prefix": token_prefix, "source_ip": source_ip}))
    except TokenExpiredError:
        return _html_response(410, "Link Expired", "This approval link has expired.")
    except TokenAlreadyUsedError:
        return _html_response(409, "Already Processed", "This action has already been processed.")
    except TokenNotFoundError:
        return _html_response(404, "Not Found", "Approval token not found.")
    except Exception:
        logger.exception(json.dumps({"message": "Token validation error", "token_prefix": token_prefix, "source_ip": source_ip}))
        return _html_response(500, "Error", "An error occurred.")

    # Execute remediation
    remediation_payload = record.get("remediation_payload", {})
    notification_context = record.get("notification_context", {})
    recipient_email = record.get("recipient_email", RECIPIENT)

    try:
        result = _invoke_remediation(AGENT_ENDPOINT, remediation_payload)
        if isinstance(result, str):
            result = {"status": "success", "actions_taken": [result[:500]], "error": None}
        status = result.get("status", "success")
        actions_taken = result.get("actions_taken", [])
        error = result.get("error")

        if status == "success":
            _update_status(token, "executed", result)
        else:
            _update_status(token, "failed", result)
    except Exception as exc:
        logger.exception(json.dumps({"message": "Remediation execution failed", "token_prefix": token_prefix}))
        status = "failed"
        actions_taken = []
        error = str(exc)
        _update_status(token, "failed", {"status": "failed", "error": error})

    # Send confirmation email
    try:
        from aha_eventbridge_lambda.ses_notifier import send_confirmation_email
        send_confirmation_email(
            recipient=recipient_email,
            sender=SES_SENDER,
            subject=f"Remediation {'Completed' if status == 'success' else 'Failed'}: {notification_context.get('affected_service', 'Unknown')}",
            status=status,
            actions_taken=actions_taken,
            error=error,
            notification_context=notification_context,
        )
    except Exception:
        logger.exception(json.dumps({"message": "Failed to send confirmation email", "token_prefix": token_prefix}))

    if status == "success":
        return _html_response(200, "Remediation Executed", "Remediation has been executed successfully. Check your email for details.")
    else:
        return _html_response(200, "Remediation Failed", f"Remediation encountered an issue: {error or 'Unknown'}. Check your email.")
