"""CloudWatch analysis tools for MIO Agent."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from mio_agent.models.assessment import AccessTier
from mio_agent.utils.aws_client import get_client
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)


def list_cloudwatch_alarms(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> list[dict[str, Any]]:
    """Enumerate all CloudWatch alarms with state and resource associations.

    Args:
        account_id: AWS account ID being assessed.
        access_tier: Access tier for authentication.
        role_arn: IAM role ARN for tier3 access.
        region: AWS region to query.

    Returns:
        List of alarm dicts with Name, StateValue, AlarmActions, Dimensions.
    """
    client = get_client("cloudwatch", access_tier=access_tier, role_arn=role_arn, region=region)
    alarms: list[dict[str, Any]] = []

    try:
        paginator = client.get_paginator("describe_alarms")
        for page in paginator.paginate():
            for alarm in page.get("MetricAlarms", []):
                alarms.append({
                    "Name": alarm.get("AlarmName"),
                    "StateValue": alarm.get("StateValue"),
                    "AlarmActions": alarm.get("AlarmActions", []),
                    "Dimensions": alarm.get("Dimensions", []),
                    "MetricName": alarm.get("MetricName"),
                    "Namespace": alarm.get("Namespace"),
                    "Threshold": alarm.get("Threshold"),
                    "ComparisonOperator": alarm.get("ComparisonOperator"),
                    "TreatMissingData": alarm.get("TreatMissingData", "missing"),
                })
        logger.info("Listed CloudWatch alarms", extra={"account_id": account_id, "count": len(alarms)})
    except ClientError as e:
        logger.warning("Failed to list CloudWatch alarms", extra={"account_id": account_id, "error": str(e)})
        raise

    return alarms


def get_metrics_coverage(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Compare running resources vs. metrics being collected.

    Returns a coverage summary including:
    - lambda_functions_with_metrics: count with custom metrics
    - ec2_instances_with_detailed_monitoring: count
    - rds_instances_with_enhanced_monitoring: count

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Coverage dict with resource counts and monitoring status.
    """
    coverage: dict[str, Any] = {
        "account_id": account_id,
        "region": region,
        "lambda": {},
        "ec2": {},
        "rds": {},
    }

    try:
        # Lambda coverage
        lambda_client = get_client("lambda", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = lambda_client.get_paginator("list_functions")
        lambda_functions = []
        for page in paginator.paginate():
            lambda_functions.extend(page.get("Functions", []))

        tracing_enabled = [
            f for f in lambda_functions
            if f.get("TracingConfig", {}).get("Mode") == "Active"
        ]
        coverage["lambda"] = {
            "total": len(lambda_functions),
            "with_xray_tracing": len(tracing_enabled),
            "without_xray_tracing": len(lambda_functions) - len(tracing_enabled),
        }
    except ClientError as e:
        logger.warning("Could not retrieve Lambda metrics coverage", extra={"error": str(e)})

    try:
        # EC2 detailed monitoring
        ec2_client = get_client("ec2", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = ec2_client.get_paginator("describe_instances")
        instances: list[dict[str, Any]] = []
        for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["running"]}]):
            for reservation in page.get("Reservations", []):
                instances.extend(reservation.get("Instances", []))

        detailed = [i for i in instances if i.get("Monitoring", {}).get("State") == "enabled"]
        coverage["ec2"] = {
            "total_running": len(instances),
            "with_detailed_monitoring": len(detailed),
            "without_detailed_monitoring": len(instances) - len(detailed),
        }
    except ClientError as e:
        logger.warning("Could not retrieve EC2 metrics coverage", extra={"error": str(e)})

    try:
        # RDS Enhanced Monitoring
        rds_client = get_client("rds", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = rds_client.get_paginator("describe_db_instances")
        rds_instances: list[dict[str, Any]] = []
        for page in paginator.paginate():
            rds_instances.extend(page.get("DBInstances", []))

        enhanced = [i for i in rds_instances if i.get("MonitoringInterval", 0) > 0]
        coverage["rds"] = {
            "total": len(rds_instances),
            "with_enhanced_monitoring": len(enhanced),
            "without_enhanced_monitoring": len(rds_instances) - len(enhanced),
        }
    except ClientError as e:
        logger.warning("Could not retrieve RDS metrics coverage", extra={"error": str(e)})

    return coverage


def analyze_log_groups(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Analyze CloudWatch Logs groups for retention and metric filters.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Analysis including retention issues and metric filter coverage.
    """
    client = get_client("logs", access_tier=access_tier, role_arn=role_arn, region=region)
    log_groups: list[dict[str, Any]] = []

    try:
        paginator = client.get_paginator("describe_log_groups")
        for page in paginator.paginate():
            log_groups.extend(page.get("logGroups", []))
    except ClientError as e:
        logger.warning("Failed to list log groups", extra={"account_id": account_id, "error": str(e)})
        raise

    no_retention = [lg for lg in log_groups if "retentionInDays" not in lg]
    short_retention = [
        lg for lg in log_groups
        if lg.get("retentionInDays", 0) < 90 and "retentionInDays" in lg
    ]

    # Check for metric filters
    groups_with_filters: set[str] = set()
    try:
        filter_paginator = client.get_paginator("describe_metric_filters")
        for page in filter_paginator.paginate():
            for mf in page.get("metricFilters", []):
                groups_with_filters.add(mf.get("logGroupName", ""))
    except ClientError:
        pass

    return {
        "account_id": account_id,
        "total_log_groups": len(log_groups),
        "without_retention_policy": len(no_retention),
        "with_short_retention_under_90_days": len(short_retention),
        "with_metric_filters": len(groups_with_filters),
        "without_metric_filters": len(log_groups) - len(groups_with_filters),
        "no_retention_examples": [lg.get("logGroupName") for lg in no_retention[:5]],
    }


def check_xray_tracing(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Check X-Ray tracing enablement per service type.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Tracing status per service with coverage gaps.
    """
    result: dict[str, Any] = {"account_id": account_id, "lambda": {}, "api_gateway": {}}

    try:
        lambda_client = get_client("lambda", access_tier=access_tier, role_arn=role_arn, region=region)
        paginator = lambda_client.get_paginator("list_functions")
        functions: list[dict[str, Any]] = []
        for page in paginator.paginate():
            functions.extend(page.get("Functions", []))

        tracing_on = [f for f in functions if f.get("TracingConfig", {}).get("Mode") == "Active"]
        not_traced = [
            {"name": f["FunctionName"], "arn": f["FunctionArn"]}
            for f in functions
            if f.get("TracingConfig", {}).get("Mode") != "Active"
        ]

        result["lambda"] = {
            "total": len(functions),
            "with_active_tracing": len(tracing_on),
            "without_tracing": len(not_traced),
            "untraced_functions": not_traced[:10],  # cap at 10 for response size
        }
    except ClientError as e:
        logger.warning("Could not check Lambda tracing", extra={"error": str(e)})

    try:
        apigw_client = get_client("apigateway", access_tier=access_tier, role_arn=role_arn, region=region)
        rest_apis = apigw_client.get_rest_apis().get("items", [])
        stages_without_tracing: list[dict[str, Any]] = []

        for api in rest_apis:
            api_id = api["id"]
            try:
                stages = apigw_client.get_stages(restApiId=api_id).get("item", [])
                for stage in stages:
                    if not stage.get("tracingEnabled", False):
                        stages_without_tracing.append({
                            "api_id": api_id,
                            "api_name": api.get("name"),
                            "stage": stage.get("stageName"),
                        })
            except ClientError:
                pass

        result["api_gateway"] = {
            "total_stages_checked": sum(1 for _ in rest_apis),
            "stages_without_tracing": len(stages_without_tracing),
            "examples": stages_without_tracing[:5],
        }
    except ClientError as e:
        logger.warning("Could not check API Gateway tracing", extra={"error": str(e)})

    return result


def validate_dashboards(
    account_id: str,
    access_tier: AccessTier,
    role_arn: str | None = None,
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Check CloudWatch dashboard existence and coverage.

    Args:
        account_id: AWS account ID.
        access_tier: Access tier.
        role_arn: IAM role ARN for tier3.
        region: AWS region.

    Returns:
        Dashboard coverage summary.
    """
    client = get_client("cloudwatch", access_tier=access_tier, role_arn=role_arn, region=region)
    dashboards: list[dict[str, Any]] = []

    try:
        paginator = client.get_paginator("list_dashboards")
        for page in paginator.paginate():
            dashboards.extend(page.get("DashboardEntries", []))
    except ClientError as e:
        logger.warning("Failed to list dashboards", extra={"error": str(e)})
        raise

    return {
        "account_id": account_id,
        "total_dashboards": len(dashboards),
        "has_dashboards": len(dashboards) > 0,
        "dashboard_names": [d.get("DashboardName") for d in dashboards],
        "last_modified": [d.get("LastModified") for d in dashboards[:5]],
    }
