# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Slack request signature verification."""

import hashlib
import hmac
import json
import logging
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def is_timestamp_valid(timestamp: str, max_age_seconds: int = 300) -> bool:
    """Check if a Slack request timestamp is within the allowed age.

    Args:
        timestamp: Unix timestamp string from the Slack request header.
        max_age_seconds: Maximum allowed age in seconds (default 300 = 5 minutes).

    Returns:
        True if the timestamp is within max_age_seconds of the current time.
    """
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        logger.warning(json.dumps({"message": "Invalid timestamp format", "timestamp": str(timestamp)}))
        return False

    age = abs(time.time() - ts)
    if age > max_age_seconds:
        logger.warning(json.dumps({
            "message": "Stale Slack request timestamp",
            "timestamp": timestamp,
            "age_seconds": round(age, 1),
            "max_age_seconds": max_age_seconds,
        }))
        return False

    return True


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: str,
    signature: str,
) -> bool:
    """Verify Slack request signature using HMAC-SHA256.

    Also checks timestamp is within 5 minutes to prevent replay attacks.

    Security: The signing_secret is stored as a Lambda environment variable
    encrypted at rest with a customer-managed AWS KMS key (KmsKeyArn in
    template.yaml). The SAM parameter uses NoEcho to prevent console exposure.
    See security/SECURITY_GUIDELINES.md "Secrets Manager" for rotation guidance.

    Args:
        signing_secret: The Slack app signing secret.
        timestamp: The X-Slack-Request-Timestamp header value.
        body: The raw request body string.
        signature: The X-Slack-Signature header value.

    Returns:
        True only if both the signature matches AND the timestamp is fresh.
    """
    if not is_timestamp_valid(timestamp):
        return False

    sig_basestring = f"v0:{timestamp}:{body}"
    computed_hash = hmac.new(
        signing_secret.encode(),
        sig_basestring.encode(),
        hashlib.sha256,
    ).hexdigest()
    computed_signature = f"v0={computed_hash}"

    match = hmac.compare_digest(computed_signature, signature)
    if not match:
        logger.warning(json.dumps({
            "message": "Slack signature verification failed",
            "timestamp": timestamp,
        }))

    return match
