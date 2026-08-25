"""Narrative Agent — converts structured findings into audience-appropriate language."""

from __future__ import annotations

import json
from uuid import uuid4

from mio_agent.guardrails.bedrock_guardrails import invoke_with_guardrails
from mio_agent.guardrails.input_validator import sanitize_narrative_input
from mio_agent.models.assessment import OMS, RiskLevel
from mio_agent.models.findings import Finding, FindingSeverity
from mio_agent.models.reports import ActionItem, CustomerReport, LeadershipSummary, TAMBrief
from mio_agent.utils.bedrock_client import DEFAULT_MODEL_ID, build_assessment_prompt, invoke_model
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


def generate_tam_brief(
    oms: OMS,
    findings: list[Finding],
    incident_context: str | None = None,
    region: str = "us-east-1",
) -> TAMBrief:
    """Generate a TAM Weekly Brief from assessment results.

    Args:
        oms: The OMS object with scores and trend.
        findings: All findings from the assessment.
        incident_context: Optional incident/support case context.
        region: AWS region for Bedrock.

    Returns:
        TAMBrief with AI-generated narrative and talking points.
    """
    top3 = _get_top_findings(findings, 3)
    findings_summary = _serialize_findings(top3)

    prompt = build_assessment_prompt(
        findings_json=findings_summary,
        audience="tam",
        account_name=oms.account_name,
        oms_score=oms.overall_oms,
        risk_level=oms.risk_level.value,
    )
    if incident_context:
        prompt += f"\n\nIncident Context: {incident_context}"

    narrative = _invoke_narrative_model(prompt, region)
    talking_points = _extract_talking_points(narrative)
    aws_services = _extract_aws_services(findings)

    return TAMBrief(
        report_id=str(uuid4()),
        assessment_id=oms.assessment_id,
        account_id=oms.account_id,
        account_name=oms.account_name,
        current_oms=oms.overall_oms,
        previous_oms=oms.previous_oms,
        trend=oms.trend,
        risk_level=oms.risk_level,
        top_3_gaps=top3,
        talking_points=talking_points,
        incident_context=incident_context,
        recommended_aws_services=aws_services,
        narrative=narrative,
    )


def generate_customer_report(
    oms: OMS,
    findings: list[Finding],
    region: str = "us-east-1",
) -> CustomerReport:
    """Generate a Customer Observability Health Report.

    Args:
        oms: The OMS object.
        findings: All findings from the assessment.
        region: AWS region for Bedrock.

    Returns:
        CustomerReport with executive summary and action plan.
    """
    findings_summary = _serialize_findings(findings)

    prompt = build_assessment_prompt(
        findings_json=findings_summary,
        audience="customer",
        account_name=oms.account_name,
        oms_score=oms.overall_oms,
        risk_level=oms.risk_level.value,
    )

    narrative = _invoke_narrative_model(prompt, region)
    exec_summary, technical_detail = _split_customer_narrative(narrative)
    action_plan = _build_action_plan(findings)

    return CustomerReport(
        report_id=str(uuid4()),
        assessment_id=oms.assessment_id,
        account_id=oms.account_id,
        account_name=oms.account_name,
        oms=oms,
        executive_summary=exec_summary,
        technical_detail=technical_detail,
        action_plan=action_plan,
    )


def generate_leadership_summary(
    account_summaries: list[dict],
    average_oms: float,
    total_accounts: int,
    region: str = "us-east-1",
) -> LeadershipSummary:
    """Generate a Leadership Portfolio Summary.

    Args:
        account_summaries: List of account OMS summaries.
        average_oms: Portfolio average OMS.
        total_accounts: Total accounts assessed.
        region: AWS region for Bedrock.

    Returns:
        LeadershipSummary with portfolio intelligence.
    """
    accounts_by_risk: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    for acct in account_summaries:
        risk = acct.get("risk_level", "MEDIUM")
        accounts_by_risk[risk] = accounts_by_risk.get(risk, 0) + 1

    prompt = f"""Generate a leadership portfolio observability summary.

Total Accounts: {total_accounts}
Portfolio Average OMS: {average_oms}/5.0
Risk Distribution: {json.dumps(accounts_by_risk)}

High/Critical Risk Accounts:
{json.dumps([a for a in account_summaries if a.get('risk_level') in ('HIGH', 'CRITICAL')][:10], default=str)}

Focus on business risk, trends, and the relationship between observability gaps and operational costs.
"""

    narrative = _invoke_narrative_model(prompt, region)

    return LeadershipSummary(
        report_id=str(uuid4()),
        total_accounts=total_accounts,
        accounts_by_risk=accounts_by_risk,
        average_oms=average_oms,
        narrative=narrative,
    )


def _invoke_narrative_model(prompt: str, region: str) -> str:
    """Invoke the Bedrock model with guardrails for narrative generation."""
    system_prompt = (
        "You are the MIO Agent narrative engine. You generate clear, evidence-based "
        "observability assessment reports. Always use specific numbers and evidence from "
        "the findings. Never include raw JSON in output. Write in professional but accessible language. "
        "Do not provide cost estimates, pricing, security vulnerability assessments, or advice "
        "outside the scope of monitoring and observability."
    )
    # Sanitize the prompt to prevent prompt injection from customer environment data
    sanitized_prompt = sanitize_narrative_input(prompt)

    try:
        return invoke_with_guardrails(
            prompt=sanitized_prompt,
            system_prompt=system_prompt,
            model_id=DEFAULT_MODEL_ID,
            max_tokens=2048,
            temperature=0.1,
            region=region,
        )
    except Exception as e:
        logger.error("Bedrock narrative generation failed", extra={"error": str(e)})
        return f"[Narrative generation unavailable: {e}]"


def _get_top_findings(findings: list[Finding], count: int) -> list[Finding]:
    """Return top N findings by severity."""
    severity_order = {
        FindingSeverity.CRITICAL: 0,
        FindingSeverity.HIGH: 1,
        FindingSeverity.MEDIUM: 2,
        FindingSeverity.LOW: 3,
        FindingSeverity.INFO: 4,
    }
    sorted_findings = sorted(findings, key=lambda f: severity_order[f.severity])
    return sorted_findings[:count]


def _serialize_findings(findings: list[Finding]) -> str:
    """Serialize findings to a prompt-friendly string."""
    items = []
    for i, f in enumerate(findings, 1):
        item = (
            f"{i}. [{f.severity.value}] {f.gap}\n"
            f"   Evidence: {f.evidence}\n"
            f"   Impact: {f.impact}\n"
            f"   Recommendation: {f.recommendation}"
        )
        items.append(item)
    return "\n\n".join(items) if items else "No findings identified."


def _extract_talking_points(narrative: str) -> list[str]:
    """Extract bullet-point talking points from narrative."""
    lines = narrative.split("\n")
    points = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("- ", "• ", "* ", "→ ")):
            points.append(stripped[2:].strip())
        elif stripped.startswith(("1.", "2.", "3.", "4.", "5.")):
            points.append(stripped[3:].strip())
    return points[:5] if points else [narrative[:200]]


def _extract_aws_services(findings: list[Finding]) -> list[str]:
    """Extract unique AWS service recommendations from findings."""
    services = set()
    for f in findings:
        if f.aws_service_recommendation:
            services.add(f.aws_service_recommendation)
    return sorted(services)


def _split_customer_narrative(narrative: str) -> tuple[str, str]:
    """Split narrative into executive summary and technical detail sections."""
    lines = narrative.split("\n")
    halfway = max(len(lines) // 3, 3)
    exec_summary = "\n".join(lines[:halfway])
    technical_detail = "\n".join(lines[halfway:])
    return exec_summary, technical_detail


def _build_action_plan(findings: list[Finding]) -> list[ActionItem]:
    """Build a prioritized action plan from findings."""
    top_findings = _get_top_findings(findings, 10)
    action_items = []

    for i, finding in enumerate(top_findings, 1):
        action_items.append(ActionItem(
            priority=i,
            title=finding.gap,
            description=finding.recommendation,
            effort=finding.effort.value,
            aws_services=[finding.aws_service_recommendation] if finding.aws_service_recommendation else [],
            expected_oms_improvement=finding.severity_weight * 0.2,
            finding_ids=[finding.finding_id],
        ))

    return action_items
