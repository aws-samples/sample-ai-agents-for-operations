"""AWS boto3 client factory with cross-account role assumption for MIO Agent."""

from __future__ import annotations

import functools
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from mio_agent.models.assessment import AccessTier
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Retry config for resilience
_BOTO_CONFIG = Config(
    retries={"max_attempts": 3, "mode": "adaptive"},
    connect_timeout=5,
    read_timeout=30,
)

# Session name for CloudTrail audit trail
_SESSION_NAME = "MIOAgentReadOnlySession"


class AWSClientError(Exception):
    """Raised when AWS client operations fail."""


class InsufficientPermissionsError(AWSClientError):
    """Raised when the assumed role lacks required permissions."""


def assume_role(role_arn: str, external_id: str | None = None) -> dict[str, Any]:
    """Assume a cross-account IAM role and return temporary credentials.

    Args:
        role_arn: ARN of the IAM role to assume.
        external_id: Optional external ID for the trust policy.

    Returns:
        Dict with AccessKeyId, SecretAccessKey, SessionToken.

    Raises:
        AWSClientError: If role assumption fails.
        InsufficientPermissionsError: If role is not assumable.
    """
    sts = boto3.client("sts", config=_BOTO_CONFIG)
    kwargs: dict[str, Any] = {
        "RoleArn": role_arn,
        "RoleSessionName": _SESSION_NAME,
        "DurationSeconds": 3600,
    }
    if external_id:
        kwargs["ExternalId"] = external_id

    try:
        response = sts.assume_role(**kwargs)
        logger.info(
            "Successfully assumed role",
            extra={"role_arn_prefix": role_arn[:40]},
        )
        return response["Credentials"]
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("AccessDenied", "AccessDeniedException"):
            raise InsufficientPermissionsError(
                f"Cannot assume role {role_arn}: {e}"
            ) from e
        raise AWSClientError(f"Failed to assume role {role_arn}: {e}") from e


def get_client(
    service: str,
    *,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
    external_id: str | None = None,
) -> Any:
    """Get a boto3 client, optionally with cross-account role assumption.

    For tier1/tier2, returns a client using the agent's own credentials.
    For tier3, assumes the customer read-only role first.

    Args:
        service: AWS service name (e.g., "cloudwatch", "logs", "xray").
        access_tier: Access tier determining authentication method.
        role_arn: Customer IAM role ARN (required for tier3).
        region: AWS region for the client.
        external_id: Optional STS external ID.

    Returns:
        boto3 service client.

    Raises:
        ValueError: If tier3 is specified without a role_arn.
        AWSClientError: If client creation fails.
    """
    if access_tier == AccessTier.TIER3:
        if not role_arn:
            raise ValueError("role_arn is required for tier3 access")
        credentials = assume_role(role_arn, external_id)
        return boto3.client(
            service,
            region_name=region,
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            config=_BOTO_CONFIG,
        )

    # Tier 1 and 2: use agent's own credentials
    return boto3.client(service, region_name=region, config=_BOTO_CONFIG)


@functools.lru_cache(maxsize=32)
def get_cached_client(
    service: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> Any:
    """Cached version of get_client for same-session reuse.

    Note: Cache is cleared between Lambda invocations automatically.
    """
    return get_client(service, access_tier=access_tier, role_arn=role_arn, region=region)


def get_account_id(role_arn: str | None = None) -> str:
    """Get the AWS account ID of the caller or assumed role.

    Args:
        role_arn: If provided, gets the account ID from the role ARN directly.

    Returns:
        12-digit AWS account ID string.
    """
    if role_arn:
        # Extract account ID from ARN: arn:aws:iam::ACCOUNT_ID:role/...
        parts = role_arn.split(":")
        if len(parts) >= 5:
            return parts[4]
    sts = boto3.client("sts", config=_BOTO_CONFIG)
    return sts.get_caller_identity()["Account"]
