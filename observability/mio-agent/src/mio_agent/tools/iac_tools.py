"""IaC scanning tools for MIO Agent — CloudFormation, CDK, Terraform, SAM."""

from __future__ import annotations

import json
from typing import Any

import yaml
from botocore.exceptions import ClientError

from mio_agent.models.assessment import AccessTier
from mio_agent.utils.aws_client import get_client
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Resource types that require monitoring definitions
MONITORING_REQUIRED_RESOURCES = {
    "AWS::Lambda::Function": {
        "tracing": "Properties.TracingConfig.Mode == 'Active'",
        "log_group": "AWS::Logs::LogGroup",
    },
    "AWS::RDS::DBInstance": {
        "enhanced_monitoring": "Properties.MonitoringInterval > 0",
        "performance_insights": "Properties.EnablePerformanceInsights == true",
    },
    "AWS::ECS::TaskDefinition": {
        "container_insights": "check_ecs_cluster_insights",
    },
    "AWS::ApiGateway::Stage": {
        "tracing": "Properties.TracingEnabled == true",
        "access_logging": "Properties.AccessLogSetting",
    },
    "AWS::ElasticLoadBalancingV2::LoadBalancer": {
        "access_logs": "Properties.LoadBalancerAttributes[AccessLogs.S3.Enabled]",
    },
}


def scan_cloudformation_stacks(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> list[dict[str, Any]]:
    """List and describe all deployed CloudFormation stacks.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        List of stack summaries with resource counts.
    """
    client = get_client("cloudformation", access_tier=access_tier, role_arn=role_arn, region=region)
    stacks: list[dict[str, Any]] = []

    try:
        paginator = client.get_paginator("list_stacks")
        for page in paginator.paginate(
            StackStatusFilter=[
                "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"
            ]
        ):
            for stack in page.get("StackSummaries", []):
                stacks.append({
                    "StackName": stack.get("StackName"),
                    "StackStatus": stack.get("StackStatus"),
                    "CreationTime": str(stack.get("CreationTime", "")),
                    "LastUpdatedTime": str(stack.get("LastUpdatedTime", "")),
                })
        logger.info("Scanned CloudFormation stacks", extra={"account_id": account_id, "count": len(stacks)})
    except ClientError as e:
        logger.warning("Failed to scan CloudFormation stacks", extra={"error": str(e)})
        raise

    return stacks


def analyze_iac_repository(template_content: str, template_format: str = "auto") -> dict[str, Any]:
    """Parse IaC template content and extract resource definitions.

    Supports: CloudFormation JSON/YAML, CDK synth output, SAM templates.
    Terraform support: expects JSON plan output.

    Args:
        template_content: Raw template file content as string.
        template_format: Format hint: "json", "yaml", "terraform", or "auto".

    Returns:
        Parsed template with resource list and monitoring coverage analysis.
    """
    parsed: dict[str, Any] = {}

    # Auto-detect format
    if template_format == "auto":
        stripped = template_content.strip()
        if stripped.startswith("{"):
            template_format = "json"
        else:
            template_format = "yaml"

    try:
        if template_format == "json":
            parsed = json.loads(template_content)
        elif template_format in ("yaml", "cloudformation", "sam"):
            parsed = yaml.safe_load(template_content)
        elif template_format == "terraform":
            parsed = json.loads(template_content)
        else:
            parsed = yaml.safe_load(template_content)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        logger.error("Failed to parse IaC template", extra={"error": str(e)})
        return {"error": str(e), "resources": [], "monitoring_coverage": {}}

    # Extract resources (CloudFormation/SAM/CDK format)
    resources: dict[str, Any] = {}
    if "Resources" in parsed:
        resources = parsed["Resources"]
    elif "resource" in parsed:
        # Terraform format
        resources = parsed["resource"]

    return {
        "template_type": _detect_template_type(parsed),
        "resource_count": len(resources),
        "resources": _extract_resource_list(resources),
        "monitoring_coverage": _check_monitoring_in_template(resources),
    }


def _detect_template_type(parsed: dict[str, Any]) -> str:
    """Detect the IaC template type."""
    if parsed.get("Transform", "").startswith("AWS::Serverless"):
        return "SAM"
    if "Resources" in parsed and "AWSTemplateFormatVersion" in parsed:
        return "CloudFormation"
    if "Resources" in parsed:
        return "CDK"
    if "resource" in parsed:
        return "Terraform"
    return "Unknown"


def _extract_resource_list(resources: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a simplified resource list from the template."""
    result = []
    for logical_id, resource_def in resources.items():
        if isinstance(resource_def, dict):
            result.append({
                "logical_id": logical_id,
                "type": resource_def.get("Type", "Unknown"),
                "has_properties": "Properties" in resource_def,
            })
    return result


def _check_monitoring_in_template(resources: dict[str, Any]) -> dict[str, Any]:
    """Check which resources have monitoring configured in the template."""
    coverage: dict[str, Any] = {}

    for logical_id, resource_def in resources.items():
        if not isinstance(resource_def, dict):
            continue
        resource_type = resource_def.get("Type", "")
        props = resource_def.get("Properties", {})

        if resource_type == "AWS::Lambda::Function":
            has_tracing = props.get("TracingConfig", {}).get("Mode") == "Active"
            coverage[logical_id] = {
                "type": resource_type,
                "xray_tracing": has_tracing,
            }
        elif resource_type == "AWS::RDS::DBInstance":
            has_enhanced = props.get("MonitoringInterval", 0) > 0
            has_pi = props.get("EnablePerformanceInsights", False)
            coverage[logical_id] = {
                "type": resource_type,
                "enhanced_monitoring": has_enhanced,
                "performance_insights": has_pi,
            }
        elif resource_type == "AWS::ApiGateway::Stage":
            has_tracing = props.get("TracingEnabled", False)
            has_access_log = "AccessLogSetting" in props
            coverage[logical_id] = {
                "type": resource_type,
                "xray_tracing": has_tracing,
                "access_logging": has_access_log,
            }

    return coverage


def identify_monitoring_gaps_in_iac(
    template_content: str,
    template_format: str = "auto",
) -> list[dict[str, Any]]:
    """Cross-reference resources vs. monitoring definitions in IaC.

    Args:
        template_content: Raw template content.
        template_format: Template format hint.

    Returns:
        List of gap dicts: resource_id, resource_type, gap_description, recommendation.
    """
    analysis = analyze_iac_repository(template_content, template_format)
    gaps: list[dict[str, Any]] = []

    for resource_id, coverage in analysis.get("monitoring_coverage", {}).items():
        resource_type = coverage.get("type", "")

        if resource_type == "AWS::Lambda::Function":
            if not coverage.get("xray_tracing", False):
                gaps.append({
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "gap": "X-Ray active tracing not enabled",
                    "severity": "HIGH",
                    "recommendation": (
                        f"Add TracingConfig: {{Mode: Active}} to {resource_id} "
                        "and attach AWSXRayDaemonWriteAccess policy."
                    ),
                })

        elif resource_type == "AWS::RDS::DBInstance":
            if not coverage.get("enhanced_monitoring", False):
                gaps.append({
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "gap": "Enhanced Monitoring not enabled (MonitoringInterval = 0)",
                    "severity": "MEDIUM",
                    "recommendation": (
                        f"Set MonitoringInterval: 60 on {resource_id} "
                        "and add a MonitoringRoleArn."
                    ),
                })
            if not coverage.get("performance_insights", False):
                gaps.append({
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "gap": "Performance Insights not enabled",
                    "severity": "MEDIUM",
                    "recommendation": (
                        f"Set EnablePerformanceInsights: true on {resource_id}."
                    ),
                })

        elif resource_type == "AWS::ApiGateway::Stage":
            if not coverage.get("xray_tracing", False):
                gaps.append({
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "gap": "X-Ray tracing not enabled on API Gateway stage",
                    "severity": "MEDIUM",
                    "recommendation": (
                        f"Set TracingEnabled: true on {resource_id}."
                    ),
                })
            if not coverage.get("access_logging", False):
                gaps.append({
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "gap": "Access logging not configured on API Gateway stage",
                    "severity": "MEDIUM",
                    "recommendation": (
                        f"Add AccessLogSetting with a CloudWatch Logs destination to {resource_id}."
                    ),
                })

    return gaps
