"""Weekly batch scheduler trigger for MIO Agent."""

from __future__ import annotations

from typing import Any

import boto3

from mio_agent.models.assessment import AccessTier, AssessmentRequest, OutputAudience, TriggerType
from mio_agent.tools.storage_tools import get_accounts_list
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

SQS_QUEUE_URL_PARAM = "/mio-agent/sqs/assessment-queue-url"


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for weekly scheduled assessments via EventBridge Scheduler.

    Runs every Monday, retrieves all configured accounts, and fans out
    assessment requests to SQS for parallel processing.

    Args:
        event: EventBridge Scheduler event.
        context: Lambda context.

    Returns:
        Handler response with enqueue count.
    """
    logger.info("Weekly batch scheduler triggered")

    try:
        accounts = get_accounts_list()
    except Exception as e:
        logger.error("Failed to retrieve accounts list", extra={"error": str(e)})
        return {"statusCode": 500, "body": f"Failed to retrieve accounts: {e}"}

    enabled_accounts = [a for a in accounts if a.get("enabled", True)]
    logger.info("Scheduling batch assessments", extra={"account_count": len(enabled_accounts)})

    ssm = boto3.client("ssm")
    queue_url = ssm.get_parameter(Name=SQS_QUEUE_URL_PARAM)["Parameter"]["Value"]
    sqs = boto3.client("sqs")

    enqueued = 0
    errors = 0

    for account in enabled_accounts:
        account_id = account.get("account_id")
        if not account_id:
            continue

        access_tier_str = account.get("access_tier", "tier1")
        try:
            access_tier = AccessTier(access_tier_str)
        except ValueError:
            access_tier = AccessTier.TIER1

        request = AssessmentRequest(
            account_id=account_id,
            account_name=account.get("account_name", f"Account {account_id}"),
            access_tier=access_tier,
            role_arn=account.get("role_arn"),
            trigger_type=TriggerType.SCHEDULED,
            requested_by=account.get("tam_alias", "mio-agent-scheduler"),
            trigger_context={"batch_run": "weekly"},
            output_audience=[OutputAudience.TAM, OutputAudience.CUSTOMER],
        )

        try:
            sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=request.model_dump_json(),
                MessageAttributes={
                    "trigger_type": {"StringValue": "scheduled", "DataType": "String"},
                    "account_id": {"StringValue": account_id, "DataType": "String"},
                },
            )
            enqueued += 1
        except Exception as e:
            logger.error("Failed to enqueue account", extra={"account_id": account_id, "error": str(e)})
            errors += 1

    logger.info("Batch scheduling complete", extra={"enqueued": enqueued, "errors": errors})
    return {
        "statusCode": 200,
        "body": f"Enqueued {enqueued} assessments ({errors} errors)",
    }
