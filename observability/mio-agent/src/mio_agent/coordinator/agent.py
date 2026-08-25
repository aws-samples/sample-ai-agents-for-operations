"""Bedrock Agent integration for MIO Agent coordinator."""

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
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


def handle_action_group(event: dict[str, Any]) -> dict[str, Any]:
    """Handle Bedrock Agent action group invocations.

    This function is the Lambda handler for Bedrock Agent action groups.
    It routes action group calls to the appropriate tool functions.

    Args:
        event: Bedrock Agent action group event.

    Returns:
        Action group response dict.
    """
    action_group = event.get("actionGroup", "")
    function_name = event.get("function", "")
    parameters = _parse_parameters(event.get("parameters", []))

    logger.info(
        "Action group invoked",
        extra={"action_group": action_group, "function": function_name},
    )

    try:
        if function_name == "run_assessment":
            result = _handle_run_assessment(parameters)
        elif function_name == "get_assessment_history":
            result = _handle_get_history(parameters)
        elif function_name == "get_accounts_list":
            result = _handle_get_accounts(parameters)
        else:
            result = {"error": f"Unknown function: {function_name}"}

        return _build_response(event, result)

    except Exception as e:
        logger.error("Action group handler failed", extra={"error": str(e)})
        return _build_response(event, {"error": str(e)})


def _handle_run_assessment(params: dict[str, Any]) -> dict[str, Any]:
    """Handle run_assessment action."""
    request = AssessmentRequest(
        account_id=params.get("account_id", ""),
        account_name=params.get("account_name", "Unknown Account"),
        access_tier=AccessTier(params.get("access_tier", "tier1")),
        role_arn=params.get("role_arn"),
        trigger_type=TriggerType.ON_DEMAND,
        requested_by=params.get("requested_by", "bedrock-agent"),
        output_audience=[OutputAudience.TAM],
    )

    result = run_assessment(request)
    return {
        "assessment_id": result.oms.assessment_id,
        "overall_oms": result.oms.overall_oms,
        "risk_level": result.oms.risk_level.value,
        "total_findings": result.oms.total_findings,
        "trend": result.oms.trend,
        "tam_brief_available": result.tam_brief is not None,
    }


def _handle_get_history(params: dict[str, Any]) -> dict[str, Any]:
    """Handle get_assessment_history action."""
    from mio_agent.tools.storage_tools import get_assessment_history
    account_id = params.get("account_id", "")
    history = get_assessment_history(account_id, limit=5)
    return {"account_id": account_id, "history": history}


def _handle_get_accounts(params: dict[str, Any]) -> dict[str, Any]:
    """Handle get_accounts_list action."""
    from mio_agent.tools.storage_tools import get_accounts_list
    accounts = get_accounts_list()
    return {"accounts": accounts, "total": len(accounts)}


def _parse_parameters(parameters: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert Bedrock action group parameter list to a flat dict."""
    return {p.get("name"): p.get("value") for p in parameters}


def _build_response(event: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Build Bedrock Agent action group response."""
    return {
        "actionGroup": event.get("actionGroup"),
        "function": event.get("function"),
        "functionResponse": {
            "responseBody": {
                "TEXT": {
                    "body": json.dumps(result, default=str),
                }
            }
        },
    }
