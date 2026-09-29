# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Event Router Lambda.

Entry point of the DevOps Agent access-key-compromise pipeline. Triggered by
three EventBridge rules (AWS Health, Security Hub/GuardDuty, Trusted Advisor)
or invoked directly for testing. It:

  1. Parses the source event into a normalized incident payload.
  2. Enriches it with account context (name, alternate contacts, tags).
  3. Starts the Step Functions state machine that drives the DevOps Agent
     investigation.

This function is READ-ONLY with respect to customer resources. It never
mutates anything; it only reads account metadata and starts the workflow.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")

sfn = boto3.client("stepfunctions")


def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event, default=str))

    source = event.get("source", "")
    detail_type = event.get("detail-type", "")

    incident = _parse_event(event, source, detail_type)
    incident = _enrich_account_context(incident)

    logger.info("Normalized incident: %s", json.dumps(incident, default=str))

    if not incident.get("access_key_id"):
        logger.warning("No access key ID extracted; investigation will scope by principal/time.")

    execution_arn = None
    if STATE_MACHINE_ARN:
        resp = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            input=json.dumps({"incident": incident}),
        )
        execution_arn = resp["executionArn"]
        logger.info("Started Step Functions execution: %s", execution_arn)
    else:
        logger.warning("STATE_MACHINE_ARN not set; returning normalized incident only.")

    return {
        "statusCode": 200,
        "incident": incident,
        "executionArn": execution_arn,
    }


# ---------------------------------------------------------------------------
# Event parsing (mirrors references/detection-sources.md)
# ---------------------------------------------------------------------------

def _parse_event(event, source, detail_type):
    detail = event.get("detail", {})
    if source == "aws.health":
        return _parse_health(event, detail)
    if source == "aws.securityhub":
        return _parse_security_hub(event, detail)
    if source == "aws.trustedadvisor":
        return _parse_trusted_advisor(event, detail)
    return _parse_manual(event)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_health(event, detail):
    access_key_id = None
    username = None
    for entity in detail.get("affectedEntities", []):
        val = entity.get("entityValue", "")
        if val.startswith("AKIA") or val.startswith("ASIA"):
            access_key_id = val
            break
        if "/" in val:
            for part in val.split("/"):
                if part.startswith("AKIA") or part.startswith("ASIA"):
                    access_key_id = part
                    break
    meta = detail.get("eventMetadata", {})
    access_key_id = access_key_id or meta.get("publicKey")
    username = meta.get("userName")
    return {
        "alert_source": "aws_health",
        "event_type": detail.get("eventTypeCode", "AWS_RISK_CREDENTIALS_EXPOSED"),
        "access_key_id": access_key_id,
        "username": username,
        "account_id": event.get("account"),
        "region": event.get("region", "us-east-1"),
        "event_time": event.get("time"),
        "detected_at": event.get("time") or _now_iso(),
    }


def _parse_security_hub(event, detail):
    findings = detail.get("findings", [])
    finding = findings[0] if findings else detail
    access_key_id = None
    username = None
    for resource in finding.get("Resources", []):
        if resource.get("Type") == "AwsIamAccessKey":
            rid = resource.get("Id", "")
            if "/" in rid:
                access_key_id = rid.split("/")[-1]
            elif ":" in rid:
                access_key_id = rid.split(":")[-1]
            else:
                access_key_id = rid
            username = resource.get("Details", {}).get("AwsIamAccessKey", {}).get("UserName")
            break
    types = finding.get("Types", [])
    return {
        "alert_source": "security_hub",
        "event_type": types[0] if types else "CompromisedCredentials",
        "access_key_id": access_key_id,
        "username": username,
        "account_id": finding.get("AwsAccountId", event.get("account")),
        "region": finding.get("Region", event.get("region", "us-east-1")),
        "event_time": finding.get("CreatedAt", event.get("time")),
        "detected_at": finding.get("CreatedAt") or event.get("time") or _now_iso(),
        "severity_hint": finding.get("Severity", {}).get("Label"),
    }


def _parse_trusted_advisor(event, detail):
    check = detail.get("check-item-detail", {})
    return {
        "alert_source": "trusted_advisor",
        "event_type": "EXPOSED_ACCESS_KEYS",
        "access_key_id": check.get("Access Key ID"),
        "username": check.get("IAM User"),
        "account_id": event.get("account"),
        "region": event.get("region", "us-east-1"),
        "event_time": event.get("time"),
        "detected_at": event.get("time") or _now_iso(),
    }


def _parse_manual(event):
    return {
        "alert_source": event.get("alert_source", "manual"),
        "event_type": event.get("event_type", "MANUAL"),
        "access_key_id": event.get("access_key_id"),
        "username": event.get("username"),
        "account_id": event.get("account_id") or event.get("account"),
        "region": event.get("region", "us-east-1"),
        "event_time": event.get("time"),
        "detected_at": event.get("time") or _now_iso(),
    }


# ---------------------------------------------------------------------------
# Account context enrichment (read-only)
# ---------------------------------------------------------------------------

def _enrich_account_context(incident):
    account_id = incident.get("account_id")
    context = {"account_name": None, "alternate_contacts": {}, "account_tags": {}}

    # Alternate contacts (Operations / Security / Billing) via the Account API.
    try:
        acct = boto3.client("account")
        for ctype in ("OPERATIONS", "SECURITY", "BILLING"):
            try:
                r = acct.get_alternate_contact(AlternateContactType=ctype)
                c = r.get("AlternateContact", {})
                context["alternate_contacts"][ctype] = {
                    "name": c.get("Name"),
                    "email": c.get("EmailAddress"),
                    "phone": c.get("PhoneNumber"),
                }
            except Exception as e:  # contact may not be set
                logger.info("No %s alternate contact: %s", ctype, e)
    except Exception as e:
        logger.info("Account API unavailable for alternate contacts: %s", e)

    # Account name + tags via Organizations (best effort; needs org access).
    if account_id:
        try:
            org = boto3.client("organizations")
            desc = org.describe_account(AccountId=account_id)
            context["account_name"] = desc.get("Account", {}).get("Name")
            context["account_email"] = desc.get("Account", {}).get("Email")
            tags = org.list_tags_for_resource(ResourceId=account_id).get("Tags", [])
            context["account_tags"] = {t["Key"]: t["Value"] for t in tags}
        except Exception as e:
            logger.info("Organizations metadata unavailable: %s", e)

    incident["account_context"] = context
    return incident
