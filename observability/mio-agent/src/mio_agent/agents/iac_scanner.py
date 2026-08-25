"""IaC Scanner Agent — scans IaC templates and produces structured findings."""

from __future__ import annotations

from typing import Any

from mio_agent.models.assessment import AccessTier
from mio_agent.models.findings import (
    Finding,
    FindingDimension,
    FindingEffort,
    FindingSeverity,
    FindingSource,
)
from mio_agent.tools.iac_tools import (
    identify_monitoring_gaps_in_iac,
    scan_cloudformation_stacks,
)
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

_SEVERITY_MAP = {
    "CRITICAL": FindingSeverity.CRITICAL,
    "HIGH": FindingSeverity.HIGH,
    "MEDIUM": FindingSeverity.MEDIUM,
    "LOW": FindingSeverity.LOW,
    "INFO": FindingSeverity.INFO,
}

_DIMENSION_MAP = {
    "distributed_tracing": FindingDimension.DISTRIBUTED_TRACING,
    "metrics_coverage": FindingDimension.METRICS_COVERAGE,
    "log_intelligence": FindingDimension.LOG_INTELLIGENCE,
    "alerting_quality": FindingDimension.ALERTING_QUALITY,
}


def scan_iac(
    template_content: str,
    template_format: str = "auto",
    account_id: str | None = None,
    access_tier: AccessTier | None = None,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> list[Finding]:
    """Scan IaC template content and return structured findings.

    Args:
        template_content: Raw IaC template string.
        template_format: Template format: json, yaml, terraform, or auto.
        account_id: Optional AWS account ID for live stack scanning.
        access_tier: Access tier (required if scanning live stacks).
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        List of findings for observability gaps in the IaC.
    """
    findings: list[Finding] = []

    gaps = identify_monitoring_gaps_in_iac(template_content, template_format)
    for gap in gaps:
        findings.append(_gap_to_finding(gap))

    logger.info(
        "IaC scan complete",
        extra={"finding_count": len(findings), "account_id": account_id},
    )
    return findings


def scan_live_stacks(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> list[Finding]:
    """Scan live CloudFormation stacks for monitoring gaps.

    This function checks stacks metadata; deep template analysis
    requires downloading individual templates.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        List of findings from live stack analysis.
    """
    findings: list[Finding] = []
    try:
        stacks = scan_cloudformation_stacks(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not scan live stacks", extra={"error": str(e)})
        return findings

    if not stacks:
        findings.append(Finding(
            dimension=FindingDimension.METRICS_COVERAGE,
            severity=FindingSeverity.INFO,
            gap="No CloudFormation stacks found in account",
            evidence="ListStacks returned 0 active stacks",
            impact="Cannot perform IaC-based monitoring gap analysis without CloudFormation stacks.",
            recommendation="If using CDK or Terraform, upload template artifacts for offline analysis.",
            effort=FindingEffort.LOW,
            source=FindingSource.IAC_SCANNER,
        ))

    return findings


def _gap_to_finding(gap: dict[str, Any]) -> Finding:
    """Convert an IaC gap dict to a structured Finding."""
    resource_id = gap.get("resource_id", "unknown")
    resource_type = gap.get("resource_type", "")
    gap_text = gap.get("gap", "Monitoring gap identified")
    severity_str = gap.get("severity", "MEDIUM")
    recommendation = gap.get("recommendation", "Review monitoring configuration.")

    # Map resource type and gap to dimension
    dimension = _infer_dimension(resource_type, gap_text)
    severity = _SEVERITY_MAP.get(severity_str, FindingSeverity.MEDIUM)

    # Build resource ARN hint from logical ID
    resource_arn = None

    return Finding(
        dimension=dimension,
        severity=severity,
        resource_arn=resource_arn,
        resource_type=resource_type,
        resource_name=resource_id,
        gap=gap_text,
        evidence=f"IaC definition for {resource_id} ({resource_type}) is missing monitoring configuration.",
        impact=_infer_impact(resource_type, gap_text),
        recommendation=recommendation,
        effort=FindingEffort.LOW,
        aws_service_recommendation=_infer_aws_service(gap_text),
        documentation_url=_infer_docs_url(resource_type, gap_text),
        source=FindingSource.IAC_SCANNER,
    )


def _infer_dimension(resource_type: str, gap_text: str) -> FindingDimension:
    """Infer the observability dimension from resource type and gap description."""
    gap_lower = gap_text.lower()
    if "tracing" in gap_lower or "xray" in gap_lower or "x-ray" in gap_lower:
        return FindingDimension.DISTRIBUTED_TRACING
    if "log" in gap_lower or "logging" in gap_lower:
        return FindingDimension.LOG_INTELLIGENCE
    if "alarm" in gap_lower or "alert" in gap_lower or "threshold" in gap_lower:
        return FindingDimension.ALERTING_QUALITY
    if "monitoring" in gap_lower or "metrics" in gap_lower or "insights" in gap_lower:
        return FindingDimension.METRICS_COVERAGE
    return FindingDimension.METRICS_COVERAGE


def _infer_impact(resource_type: str, gap_text: str) -> str:
    """Generate impact description based on resource and gap type."""
    gap_lower = gap_text.lower()
    if "tracing" in gap_lower and "lambda" in resource_type:
        return "Cannot trace Lambda invocations in distributed request flows. Increases MTTD during incidents."
    if "enhanced monitoring" in gap_lower:
        return "OS-level metrics not available for RDS instance. Cannot detect memory/CPU contention at the OS layer."
    if "performance insights" in gap_lower:
        return "Cannot identify slow SQL queries or database wait events. Database performance troubleshooting requires manual analysis."
    if "access logging" in gap_lower:
        return "API requests not logged. Cannot audit access patterns or debug 4xx/5xx error sources."
    return "Observability gap reduces ability to detect and diagnose issues quickly."


def _infer_aws_service(gap_text: str) -> str | None:
    """Map gap description to an AWS service recommendation."""
    gap_lower = gap_text.lower()
    if "tracing" in gap_lower or "x-ray" in gap_lower:
        return "AWS X-Ray"
    if "enhanced monitoring" in gap_lower or "metrics" in gap_lower:
        return "Amazon CloudWatch"
    if "performance insights" in gap_lower:
        return "Amazon RDS Performance Insights"
    if "log" in gap_lower:
        return "Amazon CloudWatch Logs"
    return "Amazon CloudWatch"


def _infer_docs_url(resource_type: str, gap_text: str) -> str | None:
    """Map to relevant documentation URL."""
    gap_lower = gap_text.lower()
    if "lambda" in resource_type and "tracing" in gap_lower:
        return "https://docs.aws.amazon.com/lambda/latest/dg/services-xray.html"
    if "rds" in resource_type and "enhanced" in gap_lower:
        return "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Monitoring.OS.html"
    if "rds" in resource_type and "performance" in gap_lower:
        return "https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PerfInsights.html"
    if "apigateway" in resource_type.lower() and "tracing" in gap_lower:
        return "https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-xray.html"
    return None
