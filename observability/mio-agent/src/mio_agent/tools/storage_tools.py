"""Storage tools for MIO Agent — DynamoDB assessment history and S3 reports."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from mio_agent.models.assessment import OMS
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

_BOTO_CONFIG = Config(retries={"max_attempts": 3, "mode": "adaptive"})

# DynamoDB table names (overridable via environment variables)
ASSESSMENTS_TABLE = "mio-agent-assessments"
ACCOUNTS_TABLE = "mio-agent-accounts"
REPORTS_BUCKET = "mio-agent-reports"


def store_assessment(
    oms: OMS,
    findings_json: str,
    region: str = "us-east-1",
) -> str:
    """Persist OMS and findings to DynamoDB.

    Args:
        oms: The OMS object to store.
        findings_json: JSON string of all findings.
        region: AWS region.

    Returns:
        Assessment ID that was stored.
    """
    client = boto3.client("dynamodb", region_name=region, config=_BOTO_CONFIG)

    item: dict[str, Any] = {
        "account_id": {"S": oms.account_id},
        "assessment_timestamp": {"S": oms.assessment_timestamp.isoformat()},
        "assessment_id": {"S": oms.assessment_id},
        "account_name": {"S": oms.account_name},
        "overall_oms": {"N": str(oms.overall_oms)},
        "risk_level": {"S": oms.risk_level.value},
        "access_tier": {"S": oms.access_tier_used.value},
        "confidence": {"S": oms.confidence.value},
        "total_findings": {"N": str(oms.total_findings)},
        "findings_json": {"S": findings_json},
        "ttl": {"N": str(int((datetime.utcnow().timestamp()) + 365 * 24 * 3600))},
    }

    if oms.previous_oms is not None:
        item["previous_oms"] = {"N": str(oms.previous_oms)}
    if oms.trend:
        item["trend"] = {"S": oms.trend}

    try:
        client.put_item(TableName=ASSESSMENTS_TABLE, Item=item)
        logger.info(
            "Stored assessment",
            extra={"account_id": oms.account_id, "assessment_id": oms.assessment_id},
        )
    except ClientError as e:
        logger.warning("Failed to store assessment", extra={"error": str(e)})
        raise

    return oms.assessment_id


def get_assessment_history(
    account_id: str,
    limit: int = 10,
    region: str = "us-east-1",
) -> list[dict[str, Any]]:
    """Retrieve previous assessments for trend analysis.

    Args:
        account_id: AWS account ID.
        limit: Maximum number of historical assessments to return.
        region: AWS region.

    Returns:
        List of assessment summaries ordered by timestamp (newest first).
    """
    client = boto3.client("dynamodb", region_name=region, config=_BOTO_CONFIG)

    try:
        response = client.query(
            TableName=ASSESSMENTS_TABLE,
            KeyConditionExpression="account_id = :pk",
            ExpressionAttributeValues={":pk": {"S": account_id}},
            ScanIndexForward=False,  # newest first
            Limit=limit,
        )
    except ClientError as e:
        logger.warning("Failed to query assessment history", extra={"error": str(e)})
        raise

    history = []
    for item in response.get("Items", []):
        history.append({
            "assessment_id": item.get("assessment_id", {}).get("S"),
            "assessment_timestamp": item.get("assessment_timestamp", {}).get("S"),
            "overall_oms": float(item.get("overall_oms", {}).get("N", 0)),
            "risk_level": item.get("risk_level", {}).get("S"),
            "total_findings": int(item.get("total_findings", {}).get("N", 0)),
        })

    return history


def get_accounts_list(region: str = "us-east-1") -> list[dict[str, Any]]:
    """Retrieve all configured customer accounts from DynamoDB.

    Args:
        region: AWS region.

    Returns:
        List of account configuration dicts.
    """
    client = boto3.client("dynamodb", region_name=region, config=_BOTO_CONFIG)

    try:
        paginator = client.get_paginator("scan")
        accounts = []
        for page in paginator.paginate(TableName=ACCOUNTS_TABLE):
            for item in page.get("Items", []):
                accounts.append({
                    "account_id": item.get("account_id", {}).get("S"),
                    "account_name": item.get("account_name", {}).get("S"),
                    "access_tier": item.get("access_tier", {}).get("S", "tier1"),
                    "role_arn": item.get("role_arn", {}).get("S"),
                    "tam_alias": item.get("tam_alias", {}).get("S"),
                    "enabled": item.get("enabled", {}).get("BOOL", True),
                })
        return accounts
    except ClientError as e:
        logger.warning("Failed to retrieve accounts list", extra={"error": str(e)})
        raise


def store_report(
    account_id: str,
    assessment_id: str,
    report_content: str,
    report_type: str,
    region: str = "us-east-1",
) -> str:
    """Upload a generated report to S3.

    Args:
        account_id: AWS account ID.
        assessment_id: Assessment ID for the report.
        report_content: Report content as string (markdown).
        report_type: Report type: tam_brief / customer_report / leadership_summary.
        region: AWS region.

    Returns:
        S3 object key of the stored report.
    """
    s3_client = boto3.client("s3", region_name=region, config=_BOTO_CONFIG)
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    key = f"reports/{account_id}/{assessment_id}/{report_type}-{timestamp}.md"

    try:
        s3_client.put_object(
            Bucket=REPORTS_BUCKET,
            Key=key,
            Body=report_content.encode("utf-8"),
            ContentType="text/markdown",
            ServerSideEncryption="AES256",
        )
        logger.info("Stored report", extra={"account_id": account_id, "key": key})
        return key
    except ClientError as e:
        logger.warning("Failed to store report", extra={"error": str(e)})
        raise


def get_report_url(
    s3_key: str,
    expiry_seconds: int = 3600,
    region: str = "us-east-1",
) -> str:
    """Generate a presigned S3 URL for report sharing.

    Args:
        s3_key: S3 object key of the report.
        expiry_seconds: URL expiry time in seconds (default 1 hour).
        region: AWS region.

    Returns:
        Presigned URL string.
    """
    s3_client = boto3.client("s3", region_name=region, config=_BOTO_CONFIG)
    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": REPORTS_BUCKET, "Key": s3_key},
            ExpiresIn=expiry_seconds,
        )
        return url
    except ClientError as e:
        logger.warning("Failed to generate presigned URL", extra={"error": str(e)})
        raise
