"""Assessment workflow orchestrator for MIO Agent."""

from __future__ import annotations

import json
import concurrent.futures

from mio_agent.agents.cloudwatch_analyst import analyze_cloudwatch
from mio_agent.agents.iac_scanner import scan_live_stacks
from mio_agent.agents.narrative import generate_customer_report, generate_tam_brief
from mio_agent.agents.third_party_validator import validate_third_party_coverage
from mio_agent.coordinator.scoring import build_oms
from mio_agent.guardrails.pipeline import PipelineResult, run_guardrail_pipeline
from mio_agent.models.assessment import AccessTier, AssessmentRequest, OMS
from mio_agent.models.findings import Finding, FindingDimension, FindingSeverity
from mio_agent.models.reports import CustomerReport, TAMBrief
from mio_agent.tools.account_tools import discover_running_services
from mio_agent.tools.storage_tools import (
    get_assessment_history,
    store_assessment,
    store_report,
)
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


class AssessmentResult:
    """Container for all outputs of an assessment run."""

    def __init__(
        self,
        oms: OMS,
        findings: list[Finding],
        tam_brief=None,
        customer_report=None,
        guardrail_result: PipelineResult | None = None,
    ) -> None:
        self.oms = oms
        self.findings = findings
        self.tam_brief = tam_brief
        self.customer_report = customer_report
        self.guardrail_result = guardrail_result


def run_assessment(
    request: AssessmentRequest,
    region: str = "us-east-1",
    bedrock_region: str = "us-east-1",
) -> AssessmentResult:
    """Execute the full assessment workflow for a customer account.

    This is the main entry point for all trigger types. It:
    1. Discovers running services in the account
    2. Runs specialist agents based on access tier
    3. Computes OMS score
    4. Generates audience-appropriate reports
    5. Persists results to DynamoDB

    Args:
        request: AssessmentRequest with account and access details.
        region: AWS region for the customer account.
        bedrock_region: AWS region for Bedrock inference.

    Returns:
        AssessmentResult with OMS, findings, and generated reports.
    """
    logger.info(
        "Starting assessment",
        extra={
            "account_id": request.account_id,
            "assessment_id": request.assessment_id,
            "access_tier": request.access_tier.value,
            "trigger_type": request.trigger_type.value,
        },
    )

    # Security: verify account is registered and enabled before proceeding.
    # This prevents the wildcard STS AssumeRole resource condition
    # (arn:aws:iam::*:role/MIOAgentReadOnly) from being exploited to access
    # unregistered accounts via caller-supplied role_arn.
    _verify_account_registered(request.account_id, region)

    # Get previous OMS for trend calculation
    previous_oms: float | None = None
    try:
        history = get_assessment_history(request.account_id, limit=1, region=region)
        if history:
            previous_oms = history[0].get("overall_oms")
    except Exception as e:
        logger.warning("Could not retrieve assessment history", extra={"error": str(e)})

    all_findings: list[Finding] = []

    # Run agents in parallel for tier2/tier3 — [PERF] significant speedup
    if request.access_tier in (AccessTier.TIER2, AccessTier.TIER3):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                "cloudwatch": executor.submit(
                    analyze_cloudwatch,
                    account_id=request.account_id,
                    access_tier=request.access_tier,
                    role_arn=request.role_arn,
                    region=region,
                ),
                "third_party": executor.submit(
                    validate_third_party_coverage,
                    account_id=request.account_id,
                    access_tier=request.access_tier,
                    role_arn=request.role_arn,
                    region=region,
                ),
                "iac": executor.submit(
                    scan_live_stacks,
                    account_id=request.account_id,
                    access_tier=request.access_tier,
                    role_arn=request.role_arn,
                    region=region,
                ),
            }
            for name, future in futures.items():
                try:
                    findings = future.result(timeout=240)
                    all_findings.extend(findings)
                    logger.info(f"{name} analysis complete", extra={"count": len(findings)})
                except Exception as e:
                    logger.error(f"{name} analysis failed", extra={"error": str(e)})

    elif request.access_tier == AccessTier.TIER1:
        # Tier 1: use only internal signals (placeholder findings)
        logger.info("Tier 1 assessment: using internal signals only")
        all_findings = _generate_tier1_findings(request.account_id)

    # Group findings by dimension for scoring
    findings_by_dimension = _group_findings_by_dimension(all_findings)

    # Build OMS
    oms = build_oms(
        assessment_id=request.assessment_id,
        account_id=request.account_id,
        account_name=request.account_name,
        findings_by_dimension=findings_by_dimension,
        access_tier=request.access_tier,
        previous_oms=previous_oms,
    )

    # Run guardrail pipeline — validates findings, checks confidence, creates review records
    guardrail_result = run_guardrail_pipeline(
        request=request,
        oms=oms,
        findings=all_findings,
        region=region,
    )

    # Use validated (guardrail-filtered) findings for report generation
    validated_findings = guardrail_result.validated_findings
    confidence_disclaimer = guardrail_result.confidence_disclaimer

    # Generate reports
    tam_brief: TAMBrief | None = None
    customer_report: CustomerReport | None = None

    from mio_agent.models.assessment import OutputAudience

    if OutputAudience.TAM in request.output_audience:
        try:
            tam_brief = generate_tam_brief(
                oms=oms,
                findings=validated_findings,
                incident_context=request.trigger_context.get("support_case_id"),
                region=bedrock_region,
            )
            # Append confidence disclaimer to narrative
            if tam_brief and confidence_disclaimer:
                tam_brief.narrative = tam_brief.narrative + f"\n\n---\n{confidence_disclaimer}"
        except Exception as e:
            logger.error("TAM brief generation failed", extra={"error": str(e)})

    if OutputAudience.CUSTOMER in request.output_audience:
        # Block customer report if confidence gate failed
        if not guardrail_result.customer_delivery_allowed:
            logger.warning(
                "Customer report blocked by confidence gate",
                extra={
                    "account_id": request.account_id,
                    "reasons": guardrail_result.gate_result.blocking_reasons,
                },
            )
        else:
            try:
                customer_report = generate_customer_report(
                    oms=oms,
                    findings=validated_findings,
                    region=bedrock_region,
                )
            except Exception as e:
                logger.error("Customer report generation failed", extra={"error": str(e)})

    # Persist results
    try:
        store_assessment(
            oms=oms,
            findings_json=json.dumps([f.model_dump(mode="json") for f in all_findings], default=str),
            region=region,
        )
    except Exception as e:
        logger.error("Failed to store assessment results", extra={"error": str(e)})

    logger.info(
        "Assessment complete",
        extra={
            "account_id": request.account_id,
            "assessment_id": request.assessment_id,
            "overall_oms": oms.overall_oms,
            "risk_level": oms.risk_level.value,
            "total_findings": len(all_findings),
        },
    )

    return AssessmentResult(
        oms=oms,
        findings=all_findings,
        tam_brief=tam_brief,
        customer_report=customer_report,
        guardrail_result=guardrail_result,
    )


def _group_findings_by_dimension(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Group findings by dimension name."""
    result: dict[str, list[Finding]] = {dim.value: [] for dim in FindingDimension}
    for finding in findings:
        result[finding.dimension.value].append(finding)
    return result


def _generate_tier1_findings(account_id: str) -> list[Finding]:
    """Generate placeholder findings for tier1 access (internal signals only).

    In production, this would query internal AWS tooling for account signals.
    """
    from mio_agent.models.findings import FindingSource, FindingSeverity
    return [
        Finding(
            dimension=FindingDimension.INCIDENT_READINESS,
            severity=FindingSeverity.INFO,
            gap="Limited assessment depth — using internal signals only",
            evidence="Access tier 1: no live account access. Assessment based on AWS internal signals.",
            impact="Assessment confidence is LOW. Upgrade to tier3 for full analysis.",
            recommendation="Request customer to grant read-only IAM role for complete assessment.",
            source=FindingSource.ACCOUNT_DISCOVERY,
        )
    ]


class AccountNotRegisteredError(Exception):
    """Raised when an assessment is requested for an unregistered account.

    This is a security control — it prevents the wildcard STS AssumeRole resource
    condition (arn:aws:iam::*:role/MIOAgentReadOnly) from being used to assume roles
    in accounts not explicitly registered and enabled in the mio-agent-accounts table.
    """


def _verify_account_registered(account_id: str, region: str = "us-east-1") -> None:
    """Verify that an account is registered and enabled before running an assessment.

    Security control addressing STRIDE threat: Elevation of Privilege via wildcard
    STS AssumeRole resource — prevents access to unregistered AWS accounts even if
    a caller supplies a valid-looking role ARN for an account not in the allowlist.

    Args:
        account_id: AWS account ID to verify.
        region: AWS region for the accounts DynamoDB table.

    Raises:
        AccountNotRegisteredError: If the account is not registered or not enabled.
        ValueError: If account_id format is invalid.
    """
    import re
    if not re.match(r"^\d{12}$", account_id):
        raise ValueError(f"Invalid account ID format: {account_id!r}")

    try:
        accounts = get_accounts_list(region=region)
    except Exception as e:
        # If we cannot reach the accounts table, fail closed — do not allow assessment.
        logger.error(  # noqa: PLE1205  # nosemgrep: logging-error-without-handling
            "Cannot verify account registration — failing closed",
            extra={"account_id": account_id, "error": str(e)},
        )
        raise AccountNotRegisteredError(
            f"Unable to verify account {account_id} registration: {e}"
        ) from e

    for account in accounts:
        if account.get("account_id") == account_id and account.get("enabled", False):
            logger.info(
                "Account registration verified",
                extra={"account_id": account_id, "access_tier": account.get("access_tier")},
            )
            return

    logger.warning(
        "Assessment rejected — account not registered or not enabled",
        extra={"account_id": account_id},
    )
    raise AccountNotRegisteredError(
        f"Account {account_id} is not registered or not enabled in mio-agent-accounts. "
        "Register the account before running an assessment."
    )
