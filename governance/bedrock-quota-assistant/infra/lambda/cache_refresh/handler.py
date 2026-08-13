"""
Refresh Lambda - Discovers Bedrock quota codes and builds customer profile snapshot.

Phase 1: Quota code discovery
  Quota codes (L-XXXXXX) are global identifiers - the same code works in any region.
  We only need to paginate ListServiceQuotas in one region (us-east-1, which has the
  most models) to discover all codes.

Phase 2: Customer profile snapshot
  Uses CloudWatch ListMetrics to discover which models have been invoked,
  classifies by access pattern (on-demand, cross-region-geo, cross-region-global),
  fetches 24h metrics and quota limits, discovers application inference profiles,
  and writes a structured JSON snapshot to DynamoDB for the agent to consume.

Triggers:
  - EventBridge schedule (weekly)
  - CloudFormation Custom Resource (on initial deployment)
  - Manual invocation: aws lambda invoke --function-name refresh-quota-cache
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta

import boto3
import boto3.dynamodb.conditions
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CACHE_TABLE_NAME = os.environ.get("CACHE_TABLE_NAME", "bedrock-quota-codes")
REFRESH_REGION = os.environ.get("REFRESH_REGION", "us-east-1")

# Regions to scan for inference profiles and CloudWatch metrics
_default_profile_regions = "us-east-1,us-west-2,eu-west-1,ap-southeast-1,ap-northeast-1"
INFERENCE_PROFILE_REGIONS = os.environ.get(
    "INFERENCE_PROFILE_REGIONS", _default_profile_regions
).split(",")

# Boto3 adaptive retry handles throttling automatically
RETRY_CONFIG = Config(retries={"max_attempts": 5, "mode": "adaptive"})

# Known geographic prefixes for cross-region inference profiles
GEO_PREFIXES = ("us.", "eu.", "ap.", "jp.")


# Prefixes in quota names that identify model inference quota types
MODEL_QUOTA_PREFIXES = {
    "on-demand model inference tokens per minute for ": "tpm",
    "on-demand model inference requests per minute for ": "rpm",
    "cross-region model inference tokens per minute for ": "cross-region-tpm",
    "cross-region model inference requests per minute for ": "cross-region-rpm",
}


def _make_sort_key(quota_name: str, quota_code: str) -> str:
    """Create a normalized DynamoDB sort key from a quota name.

    For model inference quotas, extracts the model name and appends the type:
      "On-demand model inference tokens per minute for Anthropic Claude Sonnet 4"
      → "anthropic-claude-sonnet-4#tpm"

    For other quotas, uses the quota code as suffix to guarantee uniqueness:
      "Records per batch inference job for Claude Opus 4.5"
      → "records-per-batch-inference-job-for-claude-opus-4-5#L-XXXXXX"
    """
    name = quota_name.lower()
    for prefix, qtype in MODEL_QUOTA_PREFIXES.items():
        if name.startswith(prefix):
            model_part = name[len(prefix):]
            model_key = re.sub(r"[^a-z0-9]+", "-", model_part).strip("-")
            return f"{model_key}#{qtype}"

    normalized = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
    return f"{normalized}#{quota_code}"


def refresh_quota_codes():
    """Paginate ListServiceQuotas in us-east-1 and write codes to DynamoDB."""
    sq_client = boto3.client(
        "service-quotas", region_name=REFRESH_REGION, config=RETRY_CONFIG
    )
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(CACHE_TABLE_NAME)

    paginator = sq_client.get_paginator("list_service_quotas")
    items = []
    ttl_value = int((datetime.utcnow() + timedelta(days=8)).timestamp())

    logger.info(f"Paginating ListServiceQuotas(bedrock) in {REFRESH_REGION}...")

    for page in paginator.paginate(ServiceCode="bedrock"):
        for quota in page.get("Quotas", []):
            quota_name = quota.get("QuotaName", "")
            quota_code = quota.get("QuotaCode", "")

            if not quota_name or not quota_code:
                continue

            sort_key = _make_sort_key(quota_name, quota_code)
            items.append(
                {
                    "PK": "quota",
                    "SK": sort_key,
                    "quota_code": quota_code,
                    "quota_name": quota_name,
                    "quota_name_lower": quota_name.lower(),
                    "ttl": ttl_value,
                }
            )

    logger.info(f"Discovered {len(items)} quota codes")

    # Batch write to DynamoDB (batch_writer handles 25-item batches automatically)
    with table.batch_writer() as batch:
        for item in items:
            batch.put_item(Item=item)

    # Write metadata
    table.put_item(
        Item={
            "PK": "quota",
            "SK": "metadata",
            "last_refresh": datetime.utcnow().isoformat(),
            "quota_count": len(items),
            "refresh_region": REFRESH_REGION,
            "ttl": ttl_value,
        }
    )

    logger.info(f"Wrote {len(items)} items + metadata to {CACHE_TABLE_NAME}")
    return len(items)


# ---------------------------------------------------------------------------
# Phase 2: Customer Profile Snapshot
# ---------------------------------------------------------------------------

def classify_profile_id(profile_id: str) -> tuple[str, str | None]:
    """Classify an inference profile ID by its prefix.

    Returns:
        Tuple of (pattern_type, geography) where geography is the
        prefix (e.g., "us") for geo profiles, or None otherwise.
    """
    if profile_id.startswith("global."):
        return "cross-region-global", None
    for prefix in GEO_PREFIXES:
        if profile_id.startswith(prefix):
            return "cross-region-geo", prefix.rstrip(".")
    return "on-demand", None


def _derive_display_name(model_id: str) -> str:
    """Derive a human-friendly display name from a model ID.

    Example: "anthropic.claude-sonnet-4-6-20250514-v1:0" -> "Claude Sonnet 4 6"
    """
    parts = model_id.split(".", 1)
    name_part = parts[1] if len(parts) > 1 else parts[0]
    name_part = re.sub(r"-\d{8}-v\d+:\d+$", "", name_part)
    name_part = re.sub(r"-v\d+:\d+$", "", name_part)
    name_part = re.sub(r"-v\d+$", "", name_part)
    return name_part.replace("-", " ").title()


def _derive_provider(model_id: str) -> str:
    """Derive the provider name from a model ID."""
    prefix = model_id.split(".")[0] if "." in model_id else model_id
    return prefix.title()


def _fetch_quota_limits(sq_client, pattern_type: str, base_model_id: str, quota_items: list) -> dict:
    """Fetch RPM and TPM quota limits for a model's access pattern."""
    if pattern_type == "on-demand":
        rpm_prefix = "on-demand model inference requests per minute for "
        tpm_prefix = "on-demand model inference tokens per minute for "
    else:
        rpm_prefix = "cross-region model inference requests per minute for "
        tpm_prefix = "cross-region model inference tokens per minute for "

    model_key = re.sub(r"[^a-z0-9]+", "-", base_model_id.lower()).strip("-")
    model_key = re.sub(r"-\d{8}(?=-|$)", "", model_key)
    model_key_no_version = re.sub(r"-v\d+-\d+$", "", model_key)
    model_key_no_version = re.sub(r"-v\d+$", "", model_key_no_version)

    rpm_limit = -1.0
    tpm_limit = -1.0

    for item in quota_items:
        quota_name_lower = item.get("quota_name_lower", "")
        quota_code = item.get("quota_code", "")

        is_rpm = quota_name_lower.startswith(rpm_prefix)
        is_tpm = quota_name_lower.startswith(tpm_prefix)
        if not (is_rpm or is_tpm):
            continue

        prefix_len = len(rpm_prefix) if is_rpm else len(tpm_prefix)
        quota_model_part = re.sub(
            r"[^a-z0-9]+", "-", quota_name_lower[prefix_len:]
        ).strip("-")

        if quota_model_part not in (model_key, model_key_no_version):
            continue

        try:
            resp = sq_client.get_service_quota(
                ServiceCode="bedrock", QuotaCode=quota_code
            )
            value = resp.get("Quota", {}).get("Value", -1)
            if is_rpm:
                rpm_limit = float(value)
            else:
                tpm_limit = float(value)
        except Exception as e:
            logger.warning(f"GetServiceQuota failed for {quota_code}: {e}")

    return {"rpm_limit": rpm_limit, "tpm_limit": tpm_limit}


def build_customer_profile() -> int:
    """Assemble customer profile snapshot and store in DynamoDB.

    Uses CloudWatch ListMetrics to discover which models have been invoked,
    classifies each by profile ID prefix, fetches 24h metrics and quota limits,
    discovers application inference profiles, and writes the snapshot to DynamoDB.

    Returns:
        Number of models included in the snapshot.
    """
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(CACHE_TABLE_NAME)
    now = datetime.utcnow()

    logger.info("Phase 2: Building customer profile snapshot...")

    # --- Step 1: Discover active models via ListMetrics ---
    discovered_model_ids = set()
    model_source_regions = {}  # cw_model_id -> set of source regions
    regions_scanned = set()

    for region in INFERENCE_PROFILE_REGIONS:
        region = region.strip()
        if not region:
            continue
        try:
            cw_client = boto3.client(
                "cloudwatch", region_name=region, config=RETRY_CONFIG
            )
            paginator = cw_client.get_paginator("list_metrics")
            for page in paginator.paginate(
                Namespace="AWS/Bedrock",
                MetricName="Invocations",
            ):
                for metric in page.get("Metrics", []):
                    for dim in metric.get("Dimensions", []):
                        if dim["Name"] == "ModelId" and dim["Value"]:
                            cw_id = dim["Value"]
                            discovered_model_ids.add(cw_id)
                            if cw_id not in model_source_regions:
                                model_source_regions[cw_id] = set()
                            model_source_regions[cw_id].add(region)
            regions_scanned.add(region)
        except Exception as e:
            logger.warning(f"Could not list metrics in {region}: {e}")
            continue

    logger.info(
        f"Discovered {len(discovered_model_ids)} unique ModelId dimensions "
        f"across {len(regions_scanned)} regions"
    )

    if not discovered_model_ids:
        _write_snapshot(table, now, sorted(regions_scanned), [], 0)
        return 0

    # --- Step 2: Classify and group by base model ---
    models_map = {}  # base_model_id -> model entry dict

    for cw_model_id in discovered_model_ids:
        if not cw_model_id or cw_model_id.startswith("arn:"):
            continue  # ARNs handled in app profile discovery

        pattern_type, geography = classify_profile_id(cw_model_id)

        if pattern_type == "cross-region-geo" and geography:
            base_model_id = cw_model_id[len(geography) + 1:]
        elif pattern_type == "cross-region-global":
            base_model_id = cw_model_id[len("global."):]
        else:
            base_model_id = cw_model_id

        if base_model_id not in models_map:
            models_map[base_model_id] = {
                "model_id": base_model_id,
                "display_name": _derive_display_name(base_model_id),
                "provider": _derive_provider(base_model_id),
                "active_patterns": [],
                "app_profiles": [],
            }

        existing = next(
            (p for p in models_map[base_model_id]["active_patterns"]
             if p["cw_model_id"] == cw_model_id),
            None,
        )
        if not existing:
            source_regions = sorted(model_source_regions.get(cw_model_id, set()))
            models_map[base_model_id]["active_patterns"].append({
                "pattern_type": pattern_type,
                "cw_model_id": cw_model_id,
                "source_regions": source_regions,
                "geography": geography,
                "quota_limits": {"rpm_limit": -1.0, "tpm_limit": -1.0},
                "invocations_24h": 0,
            })

    logger.info(f"Classified {len(models_map)} unique base models")

    # --- Step 3: Fetch 24h invocation counts ---
    start_time = now - timedelta(hours=24)
    for entry in models_map.values():
        for pattern in entry["active_patterns"]:
            cw_model_id = pattern["cw_model_id"]
            total = 0
            for region in pattern.get("source_regions", []):
                try:
                    cw_client = boto3.client(
                        "cloudwatch", region_name=region, config=RETRY_CONFIG
                    )
                    resp = cw_client.get_metric_statistics(
                        Namespace="AWS/Bedrock",
                        MetricName="Invocations",
                        Dimensions=[{"Name": "ModelId", "Value": cw_model_id}],
                        StartTime=start_time,
                        EndTime=now,
                        Period=86400,
                        Statistics=["Sum"],
                    )
                    for dp in resp.get("Datapoints", []):
                        total += int(dp.get("Sum", 0))
                except Exception as e:
                    logger.warning(f"CW invocations failed for {cw_model_id} in {region}: {e}")
            pattern["invocations_24h"] = total

    # --- Step 4: Fetch quota limits ---
    quota_items = []
    try:
        response = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq("quota"),
        )
        quota_items.extend(
            item for item in response.get("Items", [])
            if item.get("SK") != "metadata"
        )
        while "LastEvaluatedKey" in response:
            response = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("PK").eq("quota"),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            quota_items.extend(
                item for item in response.get("Items", [])
                if item.get("SK") != "metadata"
            )
    except Exception as e:
        logger.warning(f"Failed to query quota codes: {e}")

    if quota_items:
        try:
            sq_client = boto3.client(
                "service-quotas", region_name=REFRESH_REGION, config=RETRY_CONFIG
            )
            for entry in models_map.values():
                for pattern in entry["active_patterns"]:
                    pattern["quota_limits"] = _fetch_quota_limits(
                        sq_client, pattern["pattern_type"],
                        entry["model_id"], quota_items
                    )
        except Exception as e:
            logger.warning(f"Could not fetch quota limits: {e}")

    # --- Step 5: Discover application inference profiles ---
    for region in INFERENCE_PROFILE_REGIONS:
        region = region.strip()
        if not region:
            continue
        try:
            bedrock_client = boto3.client(
                "bedrock", region_name=region, config=RETRY_CONFIG
            )
            paginator = bedrock_client.get_paginator("list_inference_profiles")
            for page in paginator.paginate(typeEquals="APPLICATION"):
                for profile in page.get("inferenceProfileSummaries", []):
                    profile_name = profile.get("inferenceProfileName", "")
                    profile_arn = profile.get("inferenceProfileArn", "")
                    profile_id = profile.get("inferenceProfileId", "")
                    tags = {}
                    try:
                        tag_resp = bedrock_client.list_tags_for_resource(
                            resourceARN=profile_arn
                        )
                        for tag in tag_resp.get("tags", []):
                            tags[tag.get("key", "")] = tag.get("value", "")
                    except Exception:
                        pass

                    for model_ref in profile.get("models", []):
                        model_arn = model_ref.get("modelArn", "")
                        wrapped_model_id = (
                            model_arn.split("/")[-1] if "/" in model_arn else ""
                        )
                        if not wrapped_model_id:
                            continue

                        pt, _ = classify_profile_id(profile_id)
                        wraps = "cross-region" if pt != "on-demand" else "on-demand"

                        if wrapped_model_id in models_map:
                            has_cw_data = profile_arn in discovered_model_ids
                            models_map[wrapped_model_id]["app_profiles"].append({
                                "name": profile_name,
                                "arn": profile_arn,
                                "tags": tags,
                                "has_cw_data": has_cw_data,
                                "wraps": wraps,
                            })
                            break
        except Exception as e:
            logger.warning(f"Could not list application profiles in {region}: {e}")
            continue

    # --- Step 6: Write snapshot ---
    model_entries = list(models_map.values())
    _write_snapshot(table, now, sorted(regions_scanned), model_entries, len(model_entries))
    logger.info(f"Customer profile snapshot assembled with {len(model_entries)} models")
    return len(model_entries)


def _write_snapshot(table, now, regions_scanned, model_entries, model_count):
    """Write the customer profile snapshot to DynamoDB."""
    ttl_value = int((now + timedelta(days=8)).timestamp())
    snapshot = {
        "PK": "customer-profile",
        "SK": "latest",
        "snapshot_version": 1,
        "assembled_at": now.isoformat() + "Z",
        "regions_scanned": regions_scanned,
        "models": model_entries,
        "model_count": model_count,
        "ttl": ttl_value,
    }
    try:
        table.put_item(Item=json.loads(json.dumps(snapshot), parse_float=str))
    except Exception as e:
        logger.error(f"Failed to write customer profile snapshot: {e}")


def send_cfn_response(event, context, status, reason=""):
    """Send response to CloudFormation for Custom Resource."""
    import urllib.request

    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
            "PhysicalResourceId": context.log_stream_name,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
        }
    )

    response_url = event["ResponseURL"]
    if not response_url.startswith("https://"):
        raise ValueError(f"ResponseURL must use HTTPS scheme, got: {response_url[:30]}")

    req = urllib.request.Request(
        response_url,
        data=body.encode("utf-8"),
        headers={"Content-Type": ""},
        method="PUT",
    )
    urllib.request.urlopen(req)  # nosec B310 # nosemgrep: dynamic-urllib-use-detected — CFN pre-signed S3 URL, validated HTTPS above


def handler(event, context):
    """Lambda handler - supports EventBridge, manual invocation, and CloudFormation Custom Resource."""
    logger.info(f"Event: {json.dumps(event)[:500]}")

    # CloudFormation Custom Resource
    if "RequestType" in event:
        try:
            if event["RequestType"] in ("Create", "Update"):
                count = refresh_quota_codes()
                model_count = build_customer_profile()
                send_cfn_response(
                    event, context, "SUCCESS",
                    f"Cached {count} quota codes and {model_count} customer profile models"
                )
            else:
                # Delete - nothing to clean up (table deletion handled by CFN)
                send_cfn_response(event, context, "SUCCESS", "Nothing to clean up")
        except Exception as e:
            logger.error(f"Error: {e}")
            send_cfn_response(event, context, "FAILED", str(e))
        return

    # EventBridge or manual invocation
    count = refresh_quota_codes()
    model_count = build_customer_profile()
    return {
        "statusCode": 200,
        "body": f"Refreshed {count} quota codes, {model_count} customer profile models"
    }
