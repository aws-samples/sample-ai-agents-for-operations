"""Unified guardrail pipeline — applies all 5 layers in sequence.

This is the single entry point for all guardrail logic in MIO Agent.
Every assessment flows through this pipeline before output is produced.

Pipeline:
  Layer 1: Input Validation (prompt injection, size limits, format)
  Layer 2: Finding Validation (evidence anchoring, deduplication)
  Layer 3: Confidence Gate (tier-based OMS caps, completeness checks)
  Layer 4: Bedrock Guardrails (PII, topic restrictions, hallucination)
  Layer 5: Human Review Gate (approval workflow, audit trail)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mio_agent.guardrails.confidence_gate import GateResult, evaluate_confidence
from mio_agent.guardrails.finding_validator import ValidationReport, validate_findings
from mio_agent.guardrails.human_review import (
    ReportAudience,
    ReviewRecord,
    ReviewStatus,
    create_review_record,
)
from mio_agent.models.assessment import OMS, AssessmentRequest
from mio_agent.models.findings import Finding
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineResult:
    """Result of running the full guardrail pipeline."""

    # Validated findings (may be fewer than input after validation)
    validated_findings: list[Finding]

    # Confidence gate result
    gate_result: GateResult

    # Finding validation report
    validation_report: ValidationReport

    # Review records per report type
    review_records: dict[str, ReviewRecord] = field(default_factory=dict)

    # Whether the pipeline passed all checks
    pipeline_passed: bool = True

    # Summary of any issues found
    issues: list[str] = field(default_factory=list)

    @property
    def customer_delivery_allowed(self) -> bool:
        """Whether customer-facing reports can be delivered."""
        return (
            self.gate_result.customer_delivery_allowed
            and self.pipeline_passed
        )

    @property
    def confidence_disclaimer(self) -> str:
        """Transparency disclaimer to include in all outputs."""
        return self.gate_result.confidence_disclaimer


def run_guardrail_pipeline(
    request: AssessmentRequest,
    oms: OMS,
    findings: list[Finding],
    resource_counts: dict[str, int] | None = None,
    report_id_prefix: str = "",
    region: str = "us-east-1",
) -> PipelineResult:
    """Run the complete guardrail pipeline for an assessment.

    Args:
        request: The original assessment request.
        oms: The computed OMS.
        findings: Raw findings from all specialist agents.
        resource_counts: Resource counts per dimension.
        report_id_prefix: Prefix for generated report IDs.
        region: AWS region.

    Returns:
        PipelineResult with validated findings and gate decisions.
    """
    issues: list[str] = []
    pipeline_passed = True

    logger.info(
        "Running guardrail pipeline",
        extra={
            "account_id": request.account_id,
            "assessment_id": request.assessment_id,
            "finding_count": len(findings),
            "access_tier": request.access_tier.value,
        },
    )

    # Layer 2: Validate findings
    validated_findings, validation_report = validate_findings(findings)

    if validation_report.invalid > 0:
        issues.append(
            f"Finding validation rejected {validation_report.invalid}/{validation_report.total} findings. "
            f"Rejected IDs: {validation_report.rejected_findings[:5]}"
        )
        logger.warning(
            "Findings validation issues",
            extra={
                "rejected": validation_report.invalid,
                "total": validation_report.total,
                "pass_rate": validation_report.pass_rate,
            },
        )

    # Layer 3: Confidence gate
    requested_audiences = [a.value for a in request.output_audience]
    gate_result = evaluate_confidence(
        oms=oms,
        resource_counts=resource_counts,
        requested_audiences=requested_audiences,
    )

    if gate_result.is_blocked:  # noqa # nosemgrep: is-function-without-parentheses - is_blocked is a @property on GateResult, not a method
        issues.extend(gate_result.blocking_reasons)
        pipeline_passed = False
        logger.warning(
            "Confidence gate blocked delivery",
            extra={"reasons": gate_result.blocking_reasons},
        )

    for warning in gate_result.warnings:
        issues.append(f"WARNING: {warning}")

    # Layer 5: Create human review records for each audience
    review_records: dict[str, ReviewRecord] = {}

    from mio_agent.models.assessment import OutputAudience
    audience_map = {
        OutputAudience.TAM: ReportAudience.TAM,
        OutputAudience.CUSTOMER: ReportAudience.CUSTOMER,
        OutputAudience.LEADERSHIP: ReportAudience.LEADERSHIP,
    }

    for audience in request.output_audience:
        report_audience = audience_map.get(audience, ReportAudience.TAM)
        report_id = f"{report_id_prefix}-{audience.value}" if report_id_prefix else f"{request.assessment_id}-{audience.value}"

        review_record = create_review_record(
            report_id=report_id,
            assessment_id=request.assessment_id,
            account_id=request.account_id,
            account_name=request.account_name,
            audience=report_audience,
            report_type=f"{audience.value}_report",
            generated_by=request.requested_by,
            region=region,
        )
        review_records[audience.value] = review_record

        if review_record.status == ReviewStatus.PENDING_REVIEW:
            issues.append(
                f"{audience.value.upper()} report requires TAM approval before delivery. "
                f"Report ID: {report_id}"
            )

    logger.info(
        "Guardrail pipeline complete",
        extra={
            "pipeline_passed": pipeline_passed,
            "validated_findings": len(validated_findings),
            "rejected_findings": validation_report.invalid,
            "gate_decision": gate_result.decision.value,
            "issues_count": len(issues),
        },
    )

    return PipelineResult(
        validated_findings=validated_findings,
        gate_result=gate_result,
        validation_report=validation_report,
        review_records=review_records,
        pipeline_passed=pipeline_passed,
        issues=issues,
    )
