# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Routing Config Lambda handler.

Triggered by S3 ObjectCreated events on the Routing Config Bucket.
Reads the uploaded routing document, invokes Amazon Bedrock to generate
structured routing JSON, and either:
  - REQUIRE_ROUTING_APPROVAL=true: posts to Slack with an approval URL (user approves → writes to SM)
  - REQUIRE_ROUTING_APPROVAL=false: auto-approve — writes to SM immediately, notifies Slack
"""

import json
import logging
import os

from routing_config_lambda.s3_reader import read_routing_document
from routing_config_lambda.bedrock_invoker import invoke_bedrock
from routing_config_lambda.slack_notifier import post_routing_review

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"


def _generate_approval_url(routing_json: dict, source_file: str) -> str:
    """Generate a DynamoDB-backed approval token and return the approval URL."""
    from routing_approval_lambda.secrets_writer import update_routing_config  # noqa: F401
    import boto3
    import uuid
    import time

    table_name = os.environ.get("APPROVAL_TABLE_NAME", "phd-approval-store")
    approval_api_url = os.environ.get("ROUTING_APPROVAL_API_URL", "")

    token = str(uuid.uuid4())
    expires_at = int(time.time()) + 7 * 24 * 3600  # 7 days

    region = os.environ.get("AWS_REGION", "eu-west-1")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)
    table.put_item(Item={
        "token": token,
        "routing_json": json.dumps(routing_json),
        "source_file": source_file,
        "status": "pending",
        "expires_at": expires_at,
    })

    approval_url = f"{approval_api_url}/approve-routing?token={token}"
    logger.info(json.dumps({
        "message": "Approval token generated",
        "token": token,
        "source_file": source_file,
        "expires_at": expires_at,
    }))
    return approval_url


def handler(event: dict, context) -> dict:
    """Process S3 event, invoke Amazon Bedrock, post to Slack."""
    try:
        s3_record = event["Records"][0]["s3"]
        bucket = s3_record["bucket"]["name"]
        key = s3_record["object"]["key"]
    except (KeyError, IndexError) as exc:
        logger.error(json.dumps({"message": "Invalid S3 event structure", "error": str(exc)}))
        return {"statusCode": 400, "body": "Invalid event structure"}

    logger.info(json.dumps({
        "message": "Routing config Lambda invoked",
        "bucket": bucket,
        "key": key,
    }))

    # Read the uploaded document; skip if unsupported extension
    document_content = read_routing_document(bucket, key)
    if document_content is None:
        logger.info(json.dumps({
            "message": "Skipping unsupported file extension or read error",
            "bucket": bucket,
            "key": key,
        }))
        return {"statusCode": 200, "body": "Unsupported file or read error, skipped"}

    # Invoke Amazon Bedrock to generate structured routing JSON
    model_id = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
    try:
        routing_json = invoke_bedrock(document_content, model_id=model_id)
    except Exception as exc:
        logger.error(json.dumps({"message": "Amazon Bedrock invocation failed", "error": str(exc)}))
        return {"statusCode": 500, "body": "Amazon Bedrock invocation failed"}

    logger.info(json.dumps({
        "message": "Amazon Bedrock returned routing JSON",
        "service_count": len(routing_json.get("by_service", {})),
        "ou_count": len(routing_json.get("by_ou", {})),
        "default": routing_json.get("default", ""),
    }))

    require_approval = os.environ.get("REQUIRE_ROUTING_APPROVAL", "true").lower() == "true"
    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    source_file = key

    if require_approval:
        # Generate approval token and post to Slack with approval URL
        approval_url = _generate_approval_url(routing_json, source_file)
        post_routing_review(
            webhook_url=webhook_url,
            routing_json=routing_json,
            source_file=source_file,
            approval_url=approval_url,
        )
        logger.info(json.dumps({
            "message": "Routing review posted to Slack (approval required)",
            "source_file": source_file,
        }))
        return {"statusCode": 200, "body": "Routing config posted to Slack for approval"}
    else:
        # Auto-approve: write to Secrets Manager immediately
        from routing_approval_lambda.secrets_writer import update_routing_config
        secret_arn = os.environ["JIRA_SECRET_ARN"]
        update_routing_config(secret_arn, routing_json)
        logger.info(json.dumps({
            "message": "Routing config auto-applied to Secrets Manager",
            "source_file": source_file,
        }))

        # Notify Slack (informational)
        post_routing_review(
            webhook_url=webhook_url,
            routing_json=routing_json,
            source_file=source_file,
        )
        return {"statusCode": 200, "body": "Routing config auto-applied and Slack notified"}
