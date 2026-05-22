# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Approval token generation and DynamoDB storage for human-in-the-loop remediation.

IAM Permissions (defined in aha_eventbridge_lambda/template.yaml):
    {"Effect": "Allow", "Action": "dynamodb:PutItem",
     "Resource": "arn:aws:dynamodb:${Region}:${AccountId}:table/phd-approval-store"}

Data Classification and Handling:
    - Approval tokens: RESTRICTED — cryptographic secrets with 7-day TTL.
      Retention: Auto-deleted via DynamoDB TTL. Never log full token values.
    - Remediation payloads: CONFIDENTIAL — may contain resource identifiers.
      Retention: Deleted with token after 7 days. Encrypted at rest (AWS KMS CMK).
    - Recipient email: CONFIDENTIAL (PII) — deleted with token after 7 days.

Encryption at Rest:
    DynamoDB table uses customer-managed AWS KMS key (configured in template.yaml:
    SSESpecification with KMSMasterKeyId: !Ref EncryptionKey, annual rotation enabled).
"""

import os
import secrets
import time
from datetime import datetime, timezone, timedelta

import boto3
from boto3.dynamodb.conditions import Attr


def generate_approval_token() -> str:
    """Generate a URL-safe, cryptographically secure token (≥256 bits entropy).

    Uses secrets.token_urlsafe(48) which produces 48 random bytes (384 bits)
    encoded as a URL-safe base64 string of 64 characters.
    """
    return secrets.token_urlsafe(48)


def store_approval_record(
    token: str,
    remediation_payload: dict,
    notification_context: dict,
    recipient_email: str,
    ttl_days: int = 7,
) -> dict:
    """Store an approval record in DynamoDB with pending status and TTL.

    Args:
        token: The approval token (partition key).
        remediation_payload: JSON-serializable remediation actions to execute.
        notification_context: Original notification context (event_arn, service, accounts).
        recipient_email: Email address for confirmation emails.
        ttl_days: Days until token expires (default 7).

    Returns:
        Dict with token, expires_at (ISO 8601), and approval_url.

    Raises:
        RuntimeError: If token collision persists after 3 retries.
    """
    table_name = os.environ["APPROVAL_TABLE_NAME"]
    approval_api_url = os.environ["APPROVAL_API_URL"]
    region = os.environ.get("AWS_REGION", "eu-west-1")

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = int(time.time()) + (ttl_days * 86400)
    expires_at_iso = (now + timedelta(days=ttl_days)).isoformat()
    approval_url = f"{approval_api_url}/approve?token={token}"

    max_retries = 3
    current_token = token

    for attempt in range(max_retries):
        try:
            table.put_item(
                Item={
                    "token": current_token,
                    "status": "pending",
                    "remediation_payload": remediation_payload,
                    "notification_context": notification_context,
                    "recipient_email": recipient_email,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "expires_at_iso": expires_at_iso,
                },
                ConditionExpression=Attr("token").not_exists(),
            )
            return {
                "token": current_token,
                "expires_at": expires_at_iso,
                "approval_url": f"{approval_api_url}/approve?token={current_token}",
            }
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
            if attempt < max_retries - 1:
                current_token = generate_approval_token()
            else:
                raise RuntimeError(
                    "Failed to store approval record after 3 attempts due to token collisions"
                )
