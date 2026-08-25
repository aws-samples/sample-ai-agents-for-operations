"""CloudWatch Analyst Agent — analyzes CloudWatch data and produces structured findings."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from mio_agent.models.assessment import AccessTier
from mio_agent.models.findings import (
    Finding,
    FindingDimension,
    FindingEffort,
    FindingSeverity,
    FindingSource,
)
from mio_agent.tools.cloudwatch_tools import (
    analyze_log_groups,
    check_xray_tracing,
    get_metrics_coverage,
    list_cloudwatch_alarms,
    validate_dashboards,
)
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


def analyze_cloudwatch(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> list[Finding]:
    """Run full CloudWatch analysis and produce structured findings.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier for data collection.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        List of findings across all CloudWatch dimensions.
    """
    findings: list[Finding] = []

    findings.extend(_analyze_alarms(account_id, access_tier, role_arn, region))
    findings.extend(_analyze_metrics_coverage(account_id, access_tier, role_arn, region))
    findings.extend(_analyze_log_groups(account_id, access_tier, role_arn, region))
    findings.extend(_analyze_tracing(account_id, access_tier, role_arn, region))
    findings.extend(_analyze_dashboards(account_id, access_tier, role_arn, region))

    logger.info(
        "CloudWatch analysis complete",
        extra={"account_id": account_id, "finding_count": len(findings)},
    )
    return findings


def _analyze_alarms(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Generate findings for alarm coverage and quality issues."""
    findings: list[Finding] = []
    try:
        alarms = list_cloudwatch_alarms(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not analyze alarms", extra={"error": str(e)})
        return findings

    if not alarms:
        findings.append(Finding(
            dimension=FindingDimension.ALERTING_QUALITY,
            severity=FindingSeverity.CRITICAL,
            gap="No CloudWatch alarms configured",
            evidence="DescribeAlarms returned 0 alarms",
            impact="No automated detection of service degradation. All incidents will be customer-reported.",
            recommendation="Create alarms for critical metrics: Lambda errors/duration, RDS CPU/connections, API Gateway 5xx errors.",
            effort=FindingEffort.MEDIUM,
            aws_service_recommendation="Amazon CloudWatch",
            documentation_url="https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html",
            source=FindingSource.CLOUDWATCH_ANALYST,
        ))
        return findings

    # Check for alarms with no actions (silent alarms)
    silent_alarms = [a for a in alarms if not a.get("AlarmActions")]
    if silent_alarms:
        findings.append(Finding(
            dimension=FindingDimension.ALERTING_QUALITY,
            severity=FindingSeverity.HIGH,
            gap=f"{len(silent_alarms)} alarms have no actions configured",
            evidence=f"Alarms without actions: {[a['Name'] for a in silent_alarms[:5]]}",
            impact="Alarms fire but nobody is notified. Incident response relies on manual monitoring.",
            recommendation="Add SNS topic actions to all alarms to enable on-call notifications.",
            effort=FindingEffort.LOW,
            aws_service_recommendation="Amazon SNS",
            documentation_url="https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/US_AlarmAtThresholdEC2.html",
            source=FindingSource.CLOUDWATCH_ANALYST,
        ))

    # Check for alarms in INSUFFICIENT_DATA state
    insufficient = [a for a in alarms if a.get("StateValue") == "INSUFFICIENT_DATA"]
    if len(insufficient) > len(alarms) * 0.2:  # >20% in bad state
        findings.append(Finding(
            dimension=FindingDimension.ALERTING_QUALITY,
            severity=FindingSeverity.MEDIUM,
            gap=f"{len(insufficient)}/{len(alarms)} alarms in INSUFFICIENT_DATA state",
            evidence=f"Examples: {[a['Name'] for a in insufficient[:5]]}",
            impact="Alarms not receiving metric data — may indicate misconfigured metrics or missing resources.",
            recommendation="Review and fix alarms in INSUFFICIENT_DATA state. Delete stale alarms for decommissioned resources.",
            effort=FindingEffort.LOW,
            aws_service_recommendation="Amazon CloudWatch",
            source=FindingSource.CLOUDWATCH_ANALYST,
        ))

    return findings


def _analyze_metrics_coverage(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Generate findings for metrics coverage gaps."""
    findings: list[Finding] = []
    try:
        coverage = get_metrics_coverage(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not analyze metrics coverage", extra={"error": str(e)})
        return findings

    # Lambda tracing coverage
    lambda_data = coverage.get("lambda", {})
    if lambda_data:
        total = lambda_data.get("total", 0)
        without_tracing = lambda_data.get("without_xray_tracing", 0)
        if total > 0 and without_tracing > 0:
            pct = round(without_tracing / total * 100, 0)
            findings.append(Finding(
                dimension=FindingDimension.DISTRIBUTED_TRACING,
                severity=FindingSeverity.HIGH if pct > 50 else FindingSeverity.MEDIUM,
                gap=f"{without_tracing}/{total} Lambda functions without X-Ray tracing",
                evidence=f"TracingConfig.Mode != 'Active' on {without_tracing} functions ({pct}%)",
                impact="Cannot trace requests across Lambda functions. Adds significant time to root cause analysis during incidents.",
                recommendation="Enable X-Ray active tracing on all Lambda functions. Add AWSXRayDaemonWriteAccess policy.",
                effort=FindingEffort.LOW,
                aws_service_recommendation="AWS X-Ray",
                documentation_url="https://docs.aws.amazon.com/lambda/latest/dg/services-xray.html",
                source=FindingSource.CLOUDWATCH_ANALYST,
            ))

    # EC2 detailed monitoring
    ec2_data = coverage.get("ec2", {})
    if ec2_data:
        total = ec2_data.get("total_running", 0)
        without_detailed = ec2_data.get("without_detailed_monitoring", 0)
        if total > 0 and without_detailed > 0:
            findings.append(Finding(
                dimension=FindingDimension.METRICS_COVERAGE,
                severity=FindingSeverity.MEDIUM,
                gap=f"{without_detailed}/{total} EC2 instances without detailed monitoring",
                evidence=f"Monitoring.State != 'enabled' on {without_detailed} instances",
                impact="Metrics collected every 5 minutes instead of 1 minute. Reduces alarm response time.",
                recommendation="Enable detailed monitoring on all production EC2 instances.",
                effort=FindingEffort.VERY_LOW,
                aws_service_recommendation="Amazon CloudWatch",
                documentation_url="https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-cloudwatch-new.html",
                source=FindingSource.CLOUDWATCH_ANALYST,
            ))

    # RDS enhanced monitoring
    rds_data = coverage.get("rds", {})
    if rds_data:
        total = rds_data.get("total", 0)
        without_enhanced = rds_data.get("without_enhanced_monitoring", 0)
        if total > 0 and without_enhanced > 0:
            findings.append(Finding(
                dimension=FindingDimension.METRICS_COVERAGE,
                severity=FindingSeverity.MEDIUM,
                gap=f"{without_enhanced}/{total} RDS instances without Enhanced Monitoring",
                evidence=f"MonitoringInterval = 0 on {without_enhanced} RDS instances",
                impact="Cannot detect OS-level resource contention (CPU steal, memory pressure) on RDS instances.",
                recommendation="Enable Enhanced Monitoring with MonitoringInterval=60 on all RDS instances.",
                effort=FindingEffort.LOW,
                aws_service_recommendation="Amazon RDS Enhanced Monitoring",
                documentation_url="https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_Monitoring.OS.html",
                source=FindingSource.CLOUDWATCH_ANALYST,
            ))

    return findings


def _analyze_log_groups(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Generate findings for log group issues."""
    findings: list[Finding] = []
    try:
        log_analysis = analyze_log_groups(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not analyze log groups", extra={"error": str(e)})
        return findings

    without_retention = log_analysis.get("without_retention_policy", 0)
    total = log_analysis.get("total_log_groups", 0)

    if without_retention > 0:
        examples = log_analysis.get("no_retention_examples", [])
        findings.append(Finding(
            dimension=FindingDimension.LOG_INTELLIGENCE,
            severity=FindingSeverity.MEDIUM,
            gap=f"{without_retention}/{total} log groups have no retention policy",
            evidence=f"Log groups without retention: {examples}",
            impact="Logs stored indefinitely increasing costs. No log lifecycle management.",
            recommendation="Set appropriate retention periods (30-90 days for most workloads, 365+ for compliance).",
            effort=FindingEffort.VERY_LOW,
            aws_service_recommendation="Amazon CloudWatch Logs",
            documentation_url="https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/Working-with-log-groups-and-streams.html",
            source=FindingSource.CLOUDWATCH_ANALYST,
        ))

    without_filters = log_analysis.get("without_metric_filters", 0)
    if total > 0 and without_filters == total:
        findings.append(Finding(
            dimension=FindingDimension.LOG_INTELLIGENCE,
            severity=FindingSeverity.HIGH,
            gap="No metric filters on any log groups",
            evidence="DescribeMetricFilters returned 0 filters across all log groups",
            impact="Log-based errors and exceptions are not surfacing as CloudWatch metrics. Cannot alert on application errors.",
            recommendation="Create metric filters for ERROR, EXCEPTION, and FATAL log patterns. Connect to CloudWatch alarms.",
            effort=FindingEffort.MEDIUM,
            aws_service_recommendation="Amazon CloudWatch Logs Metric Filters",
            documentation_url="https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/MonitoringLogData.html",
            source=FindingSource.CLOUDWATCH_ANALYST,
        ))

    return findings


def _analyze_tracing(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Generate findings for distributed tracing coverage."""
    findings: list[Finding] = []
    try:
        tracing = check_xray_tracing(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not analyze tracing", extra={"error": str(e)})
        return findings

    api_gw = tracing.get("api_gateway", {})
    stages_without = api_gw.get("stages_without_tracing", 0)
    if stages_without > 0:
        examples = api_gw.get("examples", [])
        findings.append(Finding(
            dimension=FindingDimension.DISTRIBUTED_TRACING,
            severity=FindingSeverity.HIGH,
            gap=f"{stages_without} API Gateway stages without X-Ray tracing",
            evidence=f"TracingEnabled=false: {examples[:3]}",
            impact="API Gateway requests not traced. Cannot correlate API latency with downstream Lambda/service latency.",
            recommendation="Enable TracingEnabled on all API Gateway stages.",
            effort=FindingEffort.VERY_LOW,
            aws_service_recommendation="AWS X-Ray",
            documentation_url="https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-xray.html",
            source=FindingSource.CLOUDWATCH_ANALYST,
        ))

    return findings


def _analyze_dashboards(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None,
    region: str,
) -> list[Finding]:
    """Generate findings for dashboard coverage."""
    findings: list[Finding] = []
    try:
        dash = validate_dashboards(account_id, access_tier, role_arn, region)
    except Exception as e:
        logger.warning("Could not analyze dashboards", extra={"error": str(e)})
        return findings

    if not dash.get("has_dashboards", False):
        findings.append(Finding(
            dimension=FindingDimension.INCIDENT_READINESS,
            severity=FindingSeverity.HIGH,
            gap="No CloudWatch dashboards configured",
            evidence="ListDashboards returned 0 dashboards",
            impact="No pre-built operational visibility. During incidents, engineers must manually construct views from scratch.",
            recommendation="Create service-level dashboards covering key metrics, error rates, and latency per workload.",
            effort=FindingEffort.MEDIUM,
            aws_service_recommendation="Amazon CloudWatch Dashboards",
            documentation_url="https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Dashboards.html",
            source=FindingSource.CLOUDWATCH_ANALYST,
        ))

    return findings
