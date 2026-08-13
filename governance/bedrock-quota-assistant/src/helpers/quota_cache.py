# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Quota code cache helpers using DynamoDB."""

import re
import logging

import boto3
from botocore.exceptions import ClientError

from config import QUOTA_CACHE_TABLE, ssm_region, _RETRY_CONFIG

logger = logging.getLogger(__name__)


def _query_quota_codes(model_filter: str = None) -> list:
    """Query DynamoDB for cached quota codes, optionally filtered by model name."""
    from boto3.dynamodb.conditions import Key, Attr

    dynamodb = boto3.resource("dynamodb", region_name=ssm_region)
    table = dynamodb.Table(QUOTA_CACHE_TABLE)

    try:
        all_items = []
        query_kwargs = {
            "KeyConditionExpression": Key("PK").eq("quota"),
        }

        if model_filter:
            cleaned = model_filter.lower().strip().rstrip(".,;:!?").replace("-", " ").replace("_", " ")
            # Convert version-like "4 6" to "4.6" for matching quota names
            # (handles LLM passing "claude sonnet 4-6" which becomes "claude sonnet 4 6")
            cleaned = re.sub(r'(\d) (\d)(?=\s|$)', r'\1.\2', cleaned)
            if cleaned:
                query_kwargs["FilterExpression"] = Attr("quota_name_lower").contains(cleaned)

        while True:
            response = table.query(**query_kwargs)
            all_items.extend(response.get("Items", []))

            if "LastEvaluatedKey" not in response:
                break
            query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

        all_items = [item for item in all_items if item.get("SK") != "metadata"]

        return all_items
    except Exception as e:
        logger.error(f"DynamoDB query failed: {e}", exc_info=True)
        print(f"Warning: DynamoDB query failed: {e}")
        return []


def _fetch_live_quota_values(region: str, quota_codes: list) -> list:
    """Fetch live quota values using GetServiceQuota for each code."""
    sq_client = boto3.client("service-quotas", region_name=region, config=_RETRY_CONFIG)
    results = []

    for item in quota_codes:
        try:
            resp = sq_client.get_service_quota(
                ServiceCode="bedrock",
                QuotaCode=item["quota_code"]
            )
            quota = resp["Quota"]
            results.append({
                "name": quota.get("QuotaName", "Unknown"),
                "value": quota.get("Value", 0),
                "unit": quota.get("Unit", ""),
                "adjustable": quota.get("Adjustable", False),
            })
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchResourceException":
                continue
            elif error_code in ("TooManyRequestsException", "ThrottlingException"):
                logger.warning(f"Throttled while fetching quota {item['quota_code']}: {e}")
                results.append({
                    "name": item.get("quota_name", "Unknown"),
                    "value": "THROTTLED",
                    "unit": "",
                    "adjustable": False,
                })
            else:
                logger.warning(f"Error fetching quota {item['quota_code']}: {e}", exc_info=True)
                raise

    return results


def _filter_strict_model_match(quotas: list, display_name: str) -> list:
    """Filter quota results to exclude sub-version matches."""
    escaped = re.escape(display_name.lower())
    strict_pattern = re.compile(escaped + r"(?!\.\d)")
    return [q for q in quotas if strict_pattern.search(q["name"].lower())]


def _fallback_paginate_quotas(region: str, model_filter: str = None) -> str:
    """Fallback: paginate ListServiceQuotas when DynamoDB cache is unavailable."""
    sq_client = boto3.client("service-quotas", region_name=region, config=_RETRY_CONFIG)
    paginator = sq_client.get_paginator("list_service_quotas")

    all_quotas = []
    for page in paginator.paginate(ServiceCode="bedrock"):
        for quota in page.get("Quotas", []):
            if model_filter:
                if model_filter.lower() not in quota.get("QuotaName", "").lower():
                    continue
            all_quotas.append(quota)

    quotas_info = [f"Bedrock Quotas in {region} (direct API - cache unavailable):"]
    if model_filter:
        quotas_info.append(f"(Filtered by: {model_filter})")
    quotas_info.append(f"Found {len(all_quotas)} quotas")
    quotas_info.append("=" * 80)

    for quota in all_quotas:
        quotas_info.append(f"\n{quota.get('QuotaName', 'Unknown')}")
        quotas_info.append(f"  Current Value: {quota.get('Value', 0)}{' ' + quota.get('Unit', '') if quota.get('Unit') and quota.get('Unit') != 'None' else ''}")
        quotas_info.append(f"  Adjustable: {'Yes' if quota.get('Adjustable') else 'No'}")

    return "\n".join(quotas_info)
