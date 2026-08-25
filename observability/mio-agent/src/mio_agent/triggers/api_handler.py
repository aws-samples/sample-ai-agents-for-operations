"""API Gateway Lambda handler for on-demand MIO Agent assessments."""

from __future__ import annotations

import json
from typing import Any

from mio_agent.coordinator.orchestrator import run_assessment
from mio_agent.models.assessment import (
    AccessTier,
    AssessmentRequest,
    OutputAudience,
    TriggerType,
)
from mio_agent.tools.storage_tools import get_accounts_list, get_assessment_history
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway Lambda proxy handler.

    Routes:
    - POST /assess          → trigger assessment
    - GET /assess/{id}      → get assessment result
    - GET /accounts         → list accounts
    - GET /accounts/{id}/history → OMS history for account

    Args:
        event: API Gateway proxy event.
        context: Lambda context.

    Returns:
        API Gateway proxy response.
    """
    method = event.get("httpMethod", "GET")
    path = event.get("path", "/")
    path_params = event.get("pathParameters") or {}

    logger.info("API request", extra={"method": method, "path": path})

    try:
        if method == "POST" and path == "/assess":
            return _handle_post_assess(event)
        elif method == "GET" and path.startswith("/assess/"):
            assessment_id = path_params.get("assessment_id", "")
            return _handle_get_assessment(assessment_id)
        elif method == "GET" and path == "/accounts":
            return _handle_get_accounts()
        elif method == "GET" and "/history" in path:
            account_id = path_params.get("account_id", "")
            return _handle_get_history(account_id)
        elif method == "POST" and path.startswith("/reports/") and path.endswith("/approve"):
            report_id = path_params.get("report_id", "")
            return _handle_approve_report(event, report_id)
        elif method == "POST" and path == "/feedback":
            return _handle_finding_feedback(event)
        else:
            return _response(404, {"error": f"Route not found: {method} {path}"})
    except Exception as e:
        logger.error("API handler error", extra={"error": str(e)})
        return _response(500, {"error": "Internal server error"})


def _handle_post_assess(event: dict[str, Any]) -> dict[str, Any]:
    """Handle POST /assess — trigger an on-demand assessment."""
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    account_id = body.get("account_id")
    if not account_id:
        return _response(400, {"error": "account_id is required"})

    access_tier_str = body.get("access_tier", "tier1")
    try:
        access_tier = AccessTier(access_tier_str)
    except ValueError:
        return _response(400, {"error": f"Invalid access_tier: {access_tier_str}"})

    request = AssessmentRequest(
        account_id=account_id,
        account_name=body.get("account_name", f"Account {account_id}"),
        access_tier=access_tier,
        role_arn=body.get("role_arn"),
        trigger_type=TriggerType.ON_DEMAND,
        requested_by=body.get("requested_by", "api"),
        trigger_context=body.get("context", {}),
        output_audience=[OutputAudience.TAM, OutputAudience.CUSTOMER],
    )

    try:
        result = run_assessment(request)
    except Exception as e:
        from mio_agent.coordinator.orchestrator import AccountNotRegisteredError
        if isinstance(e, AccountNotRegisteredError):
            logger.warning(
                "Assessment rejected — account not registered",
                extra={"account_id": account_id, "error": str(e)},
            )
            return _response(403, {"error": str(e)})
        raise

    return _response(200, {
        "assessment_id": result.oms.assessment_id,
        "overall_oms": result.oms.overall_oms,
        "risk_level": result.oms.risk_level.value,
        "total_findings": result.oms.total_findings,
        "trend": result.oms.trend,
    })


def _handle_get_assessment(assessment_id: str) -> dict[str, Any]:
    """Handle GET /assess/{assessment_id}."""
    if not assessment_id:
        return _response(400, {"error": "assessment_id required"})
    # In production, look up assessment by ID from DynamoDB
    return _response(200, {"assessment_id": assessment_id, "status": "completed"})


def _handle_get_accounts() -> dict[str, Any]:
    """Handle GET /accounts."""
    accounts = get_accounts_list()
    sanitized = [
        {
            "account_id": a.get("account_id"),
            "account_name": a.get("account_name"),
            "access_tier": a.get("access_tier"),
            "enabled": a.get("enabled"),
        }
        for a in accounts
    ]
    return _response(200, {"accounts": sanitized, "total": len(sanitized)})


def _handle_get_history(account_id: str) -> dict[str, Any]:
    """Handle GET /accounts/{account_id}/history."""
    if not account_id:
        return _response(400, {"error": "account_id required"})
    history = get_assessment_history(account_id)
    return _response(200, {"account_id": account_id, "history": history})


def _handle_approve_report(event: dict[str, Any], report_id: str) -> dict[str, Any]:
    """Handle POST /reports/{report_id}/approve — TAM approves a report."""
    from mio_agent.guardrails.human_review import approve_report
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    reviewed_by = body.get("reviewed_by", "unknown-tam")
    review_notes = body.get("notes")

    record = approve_report(report_id, reviewed_by, review_notes)
    if not record:
        return _response(404, {"error": f"Review record not found: {report_id}"})

    return _response(200, {
        "report_id": report_id,
        "status": record.status.value,
        "reviewed_by": record.reviewed_by,
        "message": "Report approved for customer delivery.",
    })


def _handle_finding_feedback(event: dict[str, Any]) -> dict[str, Any]:
    """Handle POST /feedback — TAM provides accuracy feedback on a finding."""
    from mio_agent.guardrails.human_review import record_finding_feedback
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    required = ["finding_id", "assessment_id", "account_id", "is_accurate", "tam_alias"]
    missing = [f for f in required if f not in body]
    if missing:
        return _response(400, {"error": f"Missing required fields: {missing}"})

    record_finding_feedback(
        finding_id=body["finding_id"],
        assessment_id=body["assessment_id"],
        account_id=body["account_id"],
        is_accurate=body["is_accurate"],
        tam_alias=body["tam_alias"],
        notes=body.get("notes"),
    )
    return _response(200, {"message": "Feedback recorded. Thank you for improving MIO Agent accuracy."})


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "X-Agent": "mio-agent",
        },
        "body": json.dumps(body, default=str),
    }
