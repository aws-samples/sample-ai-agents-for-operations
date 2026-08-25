"""Third-party monitoring tool validation for MIO Agent."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from mio_agent.models.assessment import AccessTier
from mio_agent.utils.aws_client import get_client
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Known third-party agent identifiers in SSM inventory and EC2 tags
THIRD_PARTY_AGENTS = {
    "datadog": {
        "ssm_keys": ["datadog-agent", "Datadog Agent"],
        "tag_keys": ["datadog:monitored", "DD_ENV"],
        "lambda_layer_prefixes": ["Datadog", "datadog"],
    },
    "dynatrace": {
        "ssm_keys": ["OneAgent", "dynatrace"],
        "tag_keys": ["dt-managed", "DT_RELEASE_VERSION"],
        "lambda_layer_prefixes": ["Dynatrace", "dynatrace"],
    },
    "newrelic": {
        "ssm_keys": ["newrelic-infra", "New Relic"],
        "tag_keys": ["newrelic:monitored", "NEW_RELIC_APP_NAME"],
        "lambda_layer_prefixes": ["NewRelic", "newrelic"],
    },
    "splunk": {
        "ssm_keys": ["SplunkUniversalForwarder", "splunk"],
        "tag_keys": ["splunk:monitored"],
        "lambda_layer_prefixes": ["Splunk", "splunk"],
    },
}


def detect_third_party_agents(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Detect third-party monitoring agents via SSM inventory and EC2 tags.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Detection results per tool including instance counts.
    """
    results: dict[str, Any] = {"account_id": account_id, "detected_tools": {}}

    try:
        ec2_client = get_client("ec2", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = ec2_client.get_paginator("describe_instances")
        all_instances: list[dict[str, Any]] = []
        for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["running"]}]):
            for reservation in page.get("Reservations", []):
                all_instances.extend(reservation.get("Instances", []))

        total_instances = len(all_instances)
        results["total_ec2_instances"] = total_instances

        # Check tags for each tool
        for tool_name, config in THIRD_PARTY_AGENTS.items():
            tag_keys = config["tag_keys"]
            monitored = [
                i for i in all_instances
                if any(
                    tag.get("Key") in tag_keys
                    for tag in i.get("Tags", [])
                )
            ]
            results["detected_tools"][tool_name] = {
                "ec2_monitored": len(monitored),
                "ec2_total": total_instances,
                "ec2_coverage_pct": round(len(monitored) / max(total_instances, 1) * 100, 1),
                "detected_via_tags": len(monitored) > 0,
            }
    except ClientError as e:
        logger.warning("Could not query EC2 instances for third-party agents", extra={"error": str(e)})

    try:
        ssm_client = get_client("ssm", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = ssm_client.get_paginator("get_inventory")

        for tool_name, config in THIRD_PARTY_AGENTS.items():
            for ssm_key in config["ssm_keys"]:
                try:
                    inventory_results: list[dict[str, Any]] = []
                    for page in paginator.paginate(
                        Filters=[{"Key": "AWS:Application.Name", "Values": [ssm_key], "Type": "Contains"}]
                    ):
                        inventory_results.extend(page.get("Entities", []))

                    if inventory_results:
                        results["detected_tools"][tool_name]["ssm_instances"] = len(inventory_results)
                        results["detected_tools"][tool_name]["detected_via_ssm"] = True
                        break
                except ClientError:
                    pass
    except ClientError as e:
        logger.warning("Could not query SSM inventory", extra={"error": str(e)})

    return results


def check_metric_streams(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Validate CloudWatch metric stream forwarding configurations.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Metric stream configuration summary.
    """
    client = get_client("cloudwatch", access_tier=access_tier, role_arn=role_arn, region=region)
    streams: list[dict[str, Any]] = []

    try:
        paginator = client.get_paginator("list_metric_streams")
        for page in paginator.paginate():
            streams.extend(page.get("Entries", []))
    except ClientError as e:
        logger.warning("Could not list metric streams", extra={"error": str(e)})
        return {"account_id": account_id, "metric_streams": [], "total": 0}

    return {
        "account_id": account_id,
        "total": len(streams),
        "metric_streams": [
            {
                "name": s.get("Name"),
                "state": s.get("State"),
                "output_format": s.get("OutputFormat"),
                "firehose_arn": s.get("FirehoseArn"),
            }
            for s in streams
        ],
        "has_active_streams": any(s.get("State") == "running" for s in streams),
    }


def validate_lambda_layers(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Check Lambda layers for APM instrumentation from third-party tools.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Lambda APM instrumentation coverage per tool.
    """
    lambda_client = get_client("lambda", access_tier=access_tier, role_arn=role_arn, region=region)
    functions: list[dict[str, Any]] = []

    try:
        paginator = lambda_client.get_paginator("list_functions")
        for page in paginator.paginate():
            functions.extend(page.get("Functions", []))
    except ClientError as e:
        logger.warning("Could not list Lambda functions", extra={"error": str(e)})
        return {"account_id": account_id, "total_functions": 0, "apm_coverage": {}}

    coverage: dict[str, Any] = {tool: 0 for tool in THIRD_PARTY_AGENTS}

    for func in functions:
        layers = func.get("Layers", [])
        layer_arns = [layer.get("Arn", "") for layer in layers]
        for tool_name, config in THIRD_PARTY_AGENTS.items():
            for layer_arn in layer_arns:
                if any(prefix in layer_arn for prefix in config["lambda_layer_prefixes"]):
                    coverage[tool_name] += 1
                    break

    total = len(functions)
    return {
        "account_id": account_id,
        "total_functions": total,
        "apm_coverage": {
            tool: {
                "instrumented": count,
                "total": total,
                "coverage_pct": round(count / max(total, 1) * 100, 1),
            }
            for tool, count in coverage.items()
        },
    }


def calculate_coverage_ratio(instrumented: int, total: int) -> dict[str, Any]:
    """Calculate coverage ratio for any monitoring tool.

    Args:
        instrumented: Number of resources with monitoring.
        total: Total number of resources.

    Returns:
        Coverage stats dict.
    """
    if total == 0:
        return {"instrumented": 0, "total": 0, "coverage_pct": 0.0, "gap": 0}

    coverage_pct = round(instrumented / total * 100, 1)
    gap = total - instrumented

    return {
        "instrumented": instrumented,
        "total": total,
        "coverage_pct": coverage_pct,
        "gap": gap,
        "gap_description": f"{instrumented}/{total} resources monitored — {gap} unmonitored",
    }


def validate_tag_consistency(
    account_id: str,
    access_tier: AccessTier,
    required_tags: list[str],
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Validate required tags are present on resources for third-party tool coverage.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        required_tags: List of required tag keys.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Tag coverage summary with missing tag details.
    """
    client = get_client("resourcegroupstaggingapi", access_tier=access_tier, role_arn=role_arn, region=region)
    resources_missing_tags: list[dict[str, Any]] = []
    total_resources = 0

    try:
        paginator = client.get_paginator("get_resources")
        for page in paginator.paginate():
            for resource in page.get("ResourceTagMappingList", []):
                total_resources += 1
                existing_tag_keys = {tag["Key"] for tag in resource.get("Tags", [])}
                missing = [t for t in required_tags if t not in existing_tag_keys]
                if missing:
                    resources_missing_tags.append({
                        "arn": resource.get("ResourceARN"),
                        "missing_tags": missing,
                    })
    except ClientError as e:
        logger.warning("Could not validate tags", extra={"error": str(e)})
        return {"account_id": account_id, "total_resources": 0, "missing_tags_count": 0}

    return {
        "account_id": account_id,
        "total_resources": total_resources,
        "resources_with_all_required_tags": total_resources - len(resources_missing_tags),
        "resources_missing_tags": len(resources_missing_tags),
        "tag_coverage_pct": round(
            (total_resources - len(resources_missing_tags)) / max(total_resources, 1) * 100, 1
        ),
        "examples_missing_tags": resources_missing_tags[:5],
    }
