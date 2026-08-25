"""Account discovery tools for MIO Agent."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from mio_agent.models.assessment import AccessTier
from mio_agent.utils.aws_client import get_client
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


def discover_running_services(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Enumerate all running services in an AWS account.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Dict of service names to resource counts.
    """
    services: dict[str, Any] = {"account_id": account_id, "region": region}

    # Lambda
    try:
        client = get_client("lambda", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = client.get_paginator("list_functions")
        count = sum(len(page.get("Functions", [])) for page in paginator.paginate())
        services["lambda_functions"] = count
    except ClientError as e:
        logger.warning("Could not count Lambda functions", extra={"error": str(e)})
        services["lambda_functions"] = None

    # EC2
    try:
        client = get_client("ec2", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = client.get_paginator("describe_instances")
        count = 0
        for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["running"]}]):
            for r in page.get("Reservations", []):
                count += len(r.get("Instances", []))
        services["ec2_instances_running"] = count
    except ClientError as e:
        logger.warning("Could not count EC2 instances", extra={"error": str(e)})
        services["ec2_instances_running"] = None

    # RDS
    try:
        client = get_client("rds", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = client.get_paginator("describe_db_instances")
        count = sum(len(page.get("DBInstances", [])) for page in paginator.paginate())
        services["rds_instances"] = count
    except ClientError as e:
        logger.warning("Could not count RDS instances", extra={"error": str(e)})
        services["rds_instances"] = None

    # ECS
    try:
        client = get_client("ecs", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = client.get_paginator("list_clusters")
        cluster_arns: list[str] = []
        for page in paginator.paginate():
            cluster_arns.extend(page.get("clusterArns", []))
        services["ecs_clusters"] = len(cluster_arns)
    except ClientError as e:
        logger.warning("Could not count ECS clusters", extra={"error": str(e)})
        services["ecs_clusters"] = None

    # API Gateway
    try:
        client = get_client("apigateway", access_tier=access_tier, role_arn=role_arn, region=region)
        apis = client.get_rest_apis().get("items", [])
        services["api_gateway_rest_apis"] = len(apis)
    except ClientError as e:
        logger.warning("Could not count API Gateway APIs", extra={"error": str(e)})
        services["api_gateway_rest_apis"] = None

    # EKS
    try:
        client = get_client("eks", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = client.get_paginator("list_clusters")
        clusters: list[str] = []
        for page in paginator.paginate():
            clusters.extend(page.get("clusters", []))
        services["eks_clusters"] = len(clusters)
    except ClientError as e:
        logger.warning("Could not count EKS clusters", extra={"error": str(e)})
        services["eks_clusters"] = None

    return services


def get_resource_inventory(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
    resource_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Get full resource inventory with ARNs and tags using Resource Groups Tagging API.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.
        resource_types: Optional filter for specific resource types.

    Returns:
        List of resources with ARN and tags.
    """
    client = get_client(
        "resourcegroupstaggingapi",
        access_tier=access_tier,
        role_arn=role_arn,
        region=region,
    )
    resources: list[dict[str, Any]] = []

    try:
        kwargs: dict[str, Any] = {}
        if resource_types:
            kwargs["ResourceTypeFilters"] = resource_types

        paginator = client.get_paginator("get_resources")
        for page in paginator.paginate(**kwargs):
            for resource in page.get("ResourceTagMappingList", []):
                resources.append({
                    "arn": resource.get("ResourceARN"),
                    "tags": {tag["Key"]: tag["Value"] for tag in resource.get("Tags", [])},
                })
    except ClientError as e:
        logger.warning("Failed to get resource inventory", extra={"account_id": account_id, "error": str(e)})
        raise

    return resources


def get_tag_coverage(
    account_id: str,
    access_tier: AccessTier,
    required_tags: list[str],
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Calculate percentage of resources with required tags.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        required_tags: List of required tag key names.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Tag coverage statistics.
    """
    inventory = get_resource_inventory(account_id, access_tier, role_arn, region)
    total = len(inventory)

    if total == 0:
        return {"account_id": account_id, "total_resources": 0, "coverage_pct": 0.0}

    fully_tagged = 0
    partially_tagged = 0
    untagged = 0

    for resource in inventory:
        resource_tags = set(resource.get("tags", {}).keys())
        missing = [t for t in required_tags if t not in resource_tags]
        if not missing:
            fully_tagged += 1
        elif len(missing) < len(required_tags):
            partially_tagged += 1
        else:
            untagged += 1

    return {
        "account_id": account_id,
        "total_resources": total,
        "required_tags": required_tags,
        "fully_tagged": fully_tagged,
        "partially_tagged": partially_tagged,
        "untagged": untagged,
        "coverage_pct": round(fully_tagged / total * 100, 1),
    }
