# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""AWS Lambda handler for routing AWS Health events from EventBridge."""

import json
import logging
import os

from aha_eventbridge_lambda.event_parser import parse_health_event
from aha_eventbridge_lambda.agentcore_invoker import invoke_agentcore
from aha_eventbridge_lambda.response_parser import unwrap_response, extract_json_from_text
from aha_eventbridge_lambda.sns_publisher import publish_to_sns, publish_summary_to_sns
from aha_eventbridge_lambda.summary_formatter import format_summary

AGENT_RUNTIME_ENDPOINT_ARN = os.environ["AGENT_RUNTIME_ENDPOINT_ARN"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
IDEMPOTENCY_TABLE = os.environ.get("APPROVAL_TABLE_NAME", "")

AGENTCORE_CATEGORIES = {"issue", "investigation", "scheduledChange"}
SNS_CATEGORIES = {"accountNotification"}


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {"timestamp": self.formatTime(record, self.datefmt), "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


logger = logging.getLogger()
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
if logger.handlers:
    for h in logger.handlers:
        logger.removeHandler(h)
_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logger.addHandler(_handler)



def _build_subject(result):
    """Build SNS subject from classification result, max 100 chars."""
    if "notifications" in result and result["notifications"]:
        n = result["notifications"][0]
        return f"{n.get('classification', 'UNKNOWN')}: {n.get('affected_service', 'UNKNOWN')}"[:100]
    return f"{result.get('classification', 'UNKNOWN')}: {result.get('affected_service', 'UNKNOWN')}"[:100]

def _has_remediation_actions(classification_result: dict) -> bool:
    """Check if the classification result contains confirmed impact with remediation steps."""
    for n in classification_result.get("notifications", []):
        ia = n.get("impact_analysis")
        if ia and ia.get("impact_status") == "confirmed" and ia.get("suggested_next_steps"):
            return True
    return False


def _extract_remediation_actions(classification_result: dict) -> list[dict]:
    """Extract individual remediation actions from the classification result."""
    actions = []
    for n in classification_result.get("notifications", []):
        ia = n.get("impact_analysis")
        if ia and ia.get("impact_status") == "confirmed" and ia.get("suggested_next_steps"):
            actions.append({
                "description": f"{n.get('classification', 'UNKNOWN')}: {n.get('affected_service', 'UNKNOWN')} — {ia.get('summary', 'Remediation required')}"[:200],
                "remediation_payload": {
                    "action_type": f"{n.get('affected_service', 'unknown').lower()}_remediation",
                    "suggested_next_steps": ia["suggested_next_steps"],
                    "affected_service": n.get("affected_service"),
                    "affected_accounts": n.get("affected_accounts", []),
                    "event_type": n.get("event_type"),
                    "notification_id": n.get("notification_id"),
                },
                "notification_context": {
                    "event_arn": n.get("notification_id", ""),
                    "affected_service": n.get("affected_service", ""),
                    "affected_accounts": n.get("affected_accounts", []),
                },
            })
    return actions



def _check_idempotency(event_arn: str) -> bool:
    """Check if this event has already been processed.

    Uses a DynamoDB conditional put to atomically claim the event_arn.
    Returns True if this is a duplicate (already processed), False if first time.
    """
    if not IDEMPOTENCY_TABLE:
        return False

    import time
    import boto3
    from boto3.dynamodb.conditions import Attr

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(IDEMPOTENCY_TABLE)

    try:
        table.put_item(
            Item={
                "token": f"idempotency#{event_arn}",
                "status": "processing",
                "expires_at": int(time.time()) + 86400,
            },
            ConditionExpression=Attr("token").not_exists(),
        )
        return False
    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return True


def _load_jira_config():
    """Load Jira configuration from environment variables. Returns JiraConfig dict or None."""
    base_url = os.environ.get("JIRA_BASE_URL")
    project_key = os.environ.get("JIRA_PROJECT_KEY")
    issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
    user_email = os.environ.get("JIRA_USER_EMAIL")
    secret_arn = os.environ.get("JIRA_SECRET_ARN")
    default_assignee = os.environ.get("JIRA_DEFAULT_ASSIGNEE", "")
    team_mappings_json = os.environ.get("JIRA_TEAM_MAPPINGS", "{}")

    if not all([base_url, project_key, user_email, secret_arn]):
        logger.info(json.dumps({"message": "Jira config incomplete, skipping Jira integration"}))
        return None

    try:
        mappings = json.loads(team_mappings_json)
    except json.JSONDecodeError:
        mappings = {}

    return {
        "base_url": base_url,
        "project_key": project_key,
        "issue_type": issue_type,
        "user_email": user_email,
        "secret_arn": secret_arn,
        "default_assignee": default_assignee,
        "service_team_map": mappings.get("service_team_map", {}),
        "ou_team_map": mappings.get("ou_team_map", {}),
        "resource_team_map": mappings.get("resource_team_map", {}),
        "account_team_map": mappings.get("account_team_map", {}),
    }


def handler(event, context):
    """Lambda entry point. Parse event, route by category."""
    logger.info(json.dumps({"message": "Event received", "event_source": event.get("source", event.get("Source", "unknown"))}))

    try:
        parsed = parse_health_event(event)
    except ValueError as exc:
        logger.warning(json.dumps({"message": "Failed to parse health event", "error_type": type(exc).__name__, "error_message": str(exc), "event_arn": event.get("detail", event.get("Detail", {})).get("eventArn", "unknown") if isinstance(event.get("detail", event.get("Detail", {})), dict) else "unknown"}))
        raise

    event_arn = parsed["event_arn"]
    category = parsed["event_type_category"]
    logger.info(json.dumps({"message": "Health event parsed", "event_arn": event_arn, "event_type_category": category}))

    if _check_idempotency(event_arn):
        logger.info(json.dumps({"message": "Duplicate event skipped", "event_arn": event_arn}))
        return {"statusCode": 200, "body": "Duplicate event, already processed"}

    if category in AGENTCORE_CATEGORIES:
        logger.info(json.dumps({"message": "Routing to AgentCore", "event_arn": event_arn, "event_type_category": category}))
        try:
            result = invoke_agentcore(parsed, AGENT_RUNTIME_ENDPOINT_ARN)
            logger.info(json.dumps({"message": "AgentCore invocation completed", "event_arn": event_arn, "response_length": len(result)}))
        except Exception as exc:
            logger.warning(json.dumps({"message": "AgentCore invocation failed", "event_arn": event_arn, "error_type": type(exc).__name__, "error_message": str(exc)}))
            raise

        # Unwrap double-encoded response, extract JSON, format, publish
        try:
            text = unwrap_response(result)
            classification_result = extract_json_from_text(text)
            summary = format_summary(classification_result)
            subject = _build_subject(classification_result)

            # Skip publishing if no notifications (closed/filtered events)
            if not classification_result.get("notifications"):
                logger.info(json.dumps({"message": "No notifications to publish (event filtered/closed)", "event_arn": event_arn}))
                return {"statusCode": 200, "body": "Event filtered, no notifications to publish"}

            # Check if approval flow should be used
            remediation_mode = os.environ.get("REMEDIATION_MODE", "approval")  # "approval" or "notification"
            approval_table = os.environ.get("APPROVAL_TABLE_NAME")
            ses_sender = os.environ.get("SES_SENDER_IDENTITY")
            approval_api_url = os.environ.get("APPROVAL_API_URL")
            recipient_email = os.environ.get("NOTIFICATION_RECIPIENT_EMAIL")

            has_remediation = _has_remediation_actions(classification_result)

            # --- Jira ticket creation (for actionable classifications) ---
            _JIRA_CLASSIFICATIONS = {"SERVICE_DISRUPTION", "BREAKING_CHANGE", "SECURITY_RELATED"}
            should_create_jira = any(
                n.get("classification") in _JIRA_CLASSIFICATIONS
                for n in classification_result.get("notifications", [])
            )
            if should_create_jira:
                jira_issue_keys = []
                jira_config = _load_jira_config()
                if jira_config:
                    try:
                        from aha_eventbridge_lambda.jira_client import JiraClient
                        from aha_eventbridge_lambda.ticket_mapper import map_notification_to_jira_fields
                        from aha_eventbridge_lambda.team_router import resolve_assignee

                        client = JiraClient.from_config(jira_config)
                        for n in classification_result.get("notifications", []):
                            if n.get("classification") not in _JIRA_CLASSIFICATIONS:
                                continue
                            event_arn = n.get("notification_id", "")
                            dup_key = client.find_duplicate(jira_config["project_key"], event_arn)
                            if dup_key:
                                jira_issue_keys.append(dup_key)
                                continue
                            team_id = resolve_assignee(
                                n.get("affected_service", ""),
                                n.get("affected_accounts", []),
                                jira_config["service_team_map"],
                                jira_config["ou_team_map"],
                                jira_config["default_assignee"],
                                resource_team_map=jira_config.get("resource_team_map"),
                                account_team_map=jira_config.get("account_team_map"),
                            )
                            fields = map_notification_to_jira_fields(
                                n, jira_config["project_key"], jira_config["issue_type"], team_id=team_id,
                            )
                            result = client.create_issue(fields)
                            key = result.get("key", "")
                            jira_issue_keys.append(key)
                            logger.info(json.dumps({"message": "Jira ticket created", "issue_key": key, "event_arn": event_arn}))
                    except Exception as exc:
                        logger.warning(json.dumps({"message": "Jira ticket creation failed", "error": str(exc)}))

            # --- Slack notification (for actionable classifications) ---
            _SLACK_CLASSIFICATIONS = {"SERVICE_DISRUPTION", "BREAKING_CHANGE", "SECURITY_RELATED"}
            should_notify_slack = any(
                n.get("classification") in _SLACK_CLASSIFICATIONS
                for n in classification_result.get("notifications", [])
            )
            if should_notify_slack:
                slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")
                if slack_webhook:
                    try:
                        from routing_config_lambda.slack_notifier import post_health_event_notification
                        post_health_event_notification(
                            webhook_url=slack_webhook,
                            subject=subject,
                            summary=summary,
                            event_arn=event_arn,
                        )
                        logger.info(json.dumps({"message": "Health event notification posted to Slack", "event_arn": event_arn}))
                    except Exception as exc:
                        logger.warning(json.dumps({"message": "Slack notification failed", "event_arn": event_arn, "error": str(exc)}))

            # --- SES approval email (only in approval mode) ---
            if (has_remediation and remediation_mode == "approval"
                    and approval_table and ses_sender and recipient_email):
                try:
                    from aha_eventbridge_lambda.token_generator import generate_approval_token, store_approval_record
                    from aha_eventbridge_lambda.ses_notifier import send_approval_email

                    remediation_actions = _extract_remediation_actions(classification_result)
                    approval_actions = []
                    for action in remediation_actions:
                        token = generate_approval_token()
                        record = store_approval_record(
                            token=token,
                            remediation_payload=action["remediation_payload"],
                            notification_context=action["notification_context"],
                            recipient_email=recipient_email,
                        )
                        approval_actions.append({
                            "description": action["description"],
                            "approval_url": record["approval_url"],
                            "expires_at": record["expires_at"],
                        })

                    send_approval_email(
                        recipient=recipient_email,
                        sender=ses_sender,
                        subject=subject,
                        plain_text_body=summary,
                        approval_actions=approval_actions,
                        notification_context=remediation_actions[0]["notification_context"] if remediation_actions else {},
                    )
                    logger.info(json.dumps({"message": "Approval email sent via SES", "event_arn": event_arn, "actions_count": len(approval_actions)}))
                    return {"statusCode": 200, "body": "Approval email sent via SES"}
                except Exception as exc:
                    logger.warning(json.dumps({"message": "SES approval email failed, falling back to SNS", "event_arn": event_arn, "error": str(exc)}))

            # Default: publish plain-text summary to SNS
            publish_summary_to_sns(summary=summary, subject=subject, topic_arn=SNS_TOPIC_ARN, event_arn=event_arn)
            return {"statusCode": 200, "body": "AgentCore summary published to SNS"}
        except Exception as exc:
            logger.warning(json.dumps({"message": "Failed to format summary", "event_arn": event_arn, "error": str(exc), "response_length": len(result)}))
            text = unwrap_response(result)
            truncated = text[:200000] if len(text) > 200000 else text
            publish_summary_to_sns(summary=truncated, subject="WARNING: Agent response parse error", topic_arn=SNS_TOPIC_ARN, event_arn=event_arn)
            return {"statusCode": 200, "body": "Raw agent response published to SNS"}

    if category in SNS_CATEGORIES:
        logger.info(json.dumps({"message": "Routing to SNS", "event_arn": event_arn, "event_type_category": category}))
    else:
        logger.warning(json.dumps({"message": "Unrecognized event type category, routing to SNS", "event_arn": event_arn, "event_type_category": category}))

    try:
        publish_to_sns(parsed, SNS_TOPIC_ARN)
        logger.info(json.dumps({"message": "SNS publication completed", "event_arn": event_arn}))
        return {"statusCode": 200, "body": "SNS publication successful"}
    except Exception as exc:
        logger.warning(json.dumps({"message": "SNS publication failed", "event_arn": event_arn, "error_type": type(exc).__name__, "error_message": str(exc)}))
        raise
