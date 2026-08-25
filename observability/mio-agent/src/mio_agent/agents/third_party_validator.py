"""Third-Party Validator Agent — validates third-party monitoring tool coverage."""

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
from mio_agent.tools.third_party_tools import (
    check_metric_streams,
    detect_third_party_agents,
    validate_lambda_layers,
    validate_tag_consistency,
)
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Coverage threshold below which we raise a finding
COVERAGE_THRESHOLD_PCT = 80.0


def validate_third_party_coverage(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> list[Finding]:
    """Validate third-party monitoring tool coverage and generate findings.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        List of findings for third-party coverage gaps.
    """
    findings: list[Finding] = []

    findings.extend(_validate_agent_coverage(account_id, access_tier, role_arn, region))
    findings.extend(_validate_metric_streams(account_id, access_tier, role_arn, region))
    findings.extend(_validate_lambda_apm(account_id, access_tier, role_arn, region))

    logger.info(
        "Third-party validation complete",
        extra={"account_id": account_id, "finding_count": len(findings)},
    )
    return findings


def _validate_agent_coverage(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Check EC2 agent coverage for each detected third-party tool."""
    findings: list[Finding] = []
    try:
        detection = detect_third_party_agents(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not detect third-party agents", extra={"error": str(e)})
        return findings

    total_ec2 = detection.get("total_ec2_instances", 0)
    if total_ec2 == 0:
        return findings

    detected_tools = detection.get("detected_tools", {})

    # Check if ANY third-party tool is deployed
    any_deployed = any(
        info.get("detected_via_tags") or info.get("detected_via_ssm")
        for info in detected_tools.values()
    )

    if not any_deployed:
        # No third-party tools at all — this may be fine if using CloudWatch only
        return findings

    # For each tool that IS deployed, check coverage
    for tool_name, tool_info in detected_tools.items():
        ec2_monitored = tool_info.get("ec2_monitored", 0)
        coverage_pct = tool_info.get("ec2_coverage_pct", 0.0)
        detected = tool_info.get("detected_via_tags") or tool_info.get("detected_via_ssm")

        if detected and coverage_pct < COVERAGE_THRESHOLD_PCT:
            gap = total_ec2 - ec2_monitored
            findings.append(Finding(
                dimension=FindingDimension.METRICS_COVERAGE,
                severity=FindingSeverity.HIGH if coverage_pct < 50 else FindingSeverity.MEDIUM,
                gap=f"{tool_name.title()} agent coverage gap: {ec2_monitored}/{total_ec2} EC2 instances monitored",
                evidence=(
                    f"{tool_name.title()} detected in account but only {coverage_pct}% "
                    f"of EC2 instances have agent tags. {gap} instances unmonitored."
                ),
                impact=(
                    f"{gap} EC2 instances are not monitored by {tool_name.title()}. "
                    "Gaps in third-party monitoring create blind spots during incidents."
                ),
                recommendation=(
                    f"Deploy {tool_name.title()} agent to all {gap} unmonitored EC2 instances. "
                    "Use Systems Manager Run Command or user data scripts for automated deployment."
                ),
                effort=FindingEffort.MEDIUM,
                aws_service_recommendation="AWS Systems Manager",
                source=FindingSource.THIRD_PARTY_VALIDATOR,
            ))

    return findings


def _validate_metric_streams(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Check CloudWatch metric stream configuration for third-party forwarding."""
    findings: list[Finding] = []
    try:
        streams = check_metric_streams(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not check metric streams", extra={"error": str(e)})
        return findings

    stream_list = streams.get("metric_streams", [])
    if not stream_list:
        return findings  # No streams — not necessarily a gap unless third-party tool expected

    # Check for inactive streams
    inactive = [s for s in stream_list if s.get("state") != "running"]
    if inactive:
        findings.append(Finding(
            dimension=FindingDimension.METRICS_COVERAGE,
            severity=FindingSeverity.MEDIUM,
            gap=f"{len(inactive)} CloudWatch metric streams are not running",
            evidence=f"Inactive streams: {[s.get('name') for s in inactive]}",
            impact="CloudWatch metrics not forwarding to third-party monitoring platform. Dashboards may show stale data.",
            recommendation="Investigate and restart inactive metric streams. Check Kinesis Firehose delivery health.",
            effort=FindingEffort.LOW,
            aws_service_recommendation="Amazon Kinesis Data Firehose",
            documentation_url="https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-metric-streams.html",
            source=FindingSource.THIRD_PARTY_VALIDATOR,
        ))

    return findings


def _validate_lambda_apm(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Check Lambda APM instrumentation via layers."""
    findings: list[Finding] = []
    try:
        layer_coverage = validate_lambda_layers(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not validate Lambda layers", extra={"error": str(e)})
        return findings

    total = layer_coverage.get("total_functions", 0)
    if total == 0:
        return findings

    apm_coverage = layer_coverage.get("apm_coverage", {})

    # Only flag if a tool has partial coverage (is deployed but incomplete)
    for tool_name, coverage in apm_coverage.items():
        instrumented = coverage.get("instrumented", 0)
        coverage_pct = coverage.get("coverage_pct", 0.0)

        # Only report if tool is partially deployed (>0 but <80%)
        if 0 < instrumented < total and coverage_pct < COVERAGE_THRESHOLD_PCT:
            gap = total - instrumented
            findings.append(Finding(
                dimension=FindingDimension.DISTRIBUTED_TRACING,
                severity=FindingSeverity.MEDIUM,
                gap=(
                    f"{tool_name.title()} APM layer coverage gap: "
                    f"{instrumented}/{total} Lambda functions instrumented"
                ),
                evidence=f"{tool_name.title()} Lambda layer found on {instrumented} functions, {gap} uninstrumented.",
                impact=f"{gap} Lambda functions missing {tool_name.title()} APM instrumentation. Distributed traces will be incomplete.",
                recommendation=(
                    f"Add {tool_name.title()} Lambda layer to all {gap} uninstrumented functions. "
                    "Use Lambda function configurations or CDK constructs for consistent deployment."
                ),
                effort=FindingEffort.LOW,
                source=FindingSource.THIRD_PARTY_VALIDATOR,
            ))

    return findings
