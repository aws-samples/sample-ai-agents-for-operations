# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: list_active_bedrock_models — discovers which models have been used recently."""

import logging
from datetime import datetime, timedelta

import boto3
from strands import tool

from config import DEFAULT_REGION
from models import get_model_info
from tools.list_active_inference_profiles import _list_active_inference_profiles_impl

logger = logging.getLogger(__name__)


@tool
def list_active_bedrock_models(
    region: str = DEFAULT_REGION,
    hours_back: int = 24,
    by_profile: bool = False,
) -> str:
    """Discover which Bedrock models have been used recently and rank them.

    This is the DEFAULT tool for model-centric questions like "which model have
    I used most?" or "what did I run yesterday?". It answers at MODEL grain.

    When by_profile=True, returns the same discovery at PROFILE grain, applying
    the asymmetric inclusion rule (System_Defined_Profiles need >= 5
    invocations; Application_Profiles always shown with a "(no recent usage)"
    label when below threshold). Use by_profile=True ONLY when the user asks
    explicitly for a per-profile or per-application view.

    Args:
        region: AWS region (default: agent's deployed region).
        hours_back: Lookback window in hours (default: 24).
        by_profile: When True, return profile-grain output instead of model-grain.

    Returns:
        Ranked list of models (or profiles, when by_profile=True) by invocation count.
    """
    # Profile-grain branch — delegate to the shared impl so this tool and
    # list_active_inference_profiles stay in lockstep (Req 3.10, Tasks 6.2).
    # show_all_system_profiles is pinned to False here because the LLM surface
    # for by_profile=True on list_active_bedrock_models is intentionally the
    # default (noise-reduced) view; callers who want the exhaustive listing go
    # through list_active_inference_profiles(show_all_system_profiles=True).
    if by_profile:
        return _list_active_inference_profiles_impl(
            region=region,
            hours_back=hours_back,
            show_all_system_profiles=False,
        )

    # Model-grain branch (Req 3.7).
    try:
        cw = boto3.client("cloudwatch", region_name=region)

        # Step 1: Discover all model IDs with Invocations metrics
        paginator = cw.get_paginator("list_metrics")
        model_ids = set()

        for page in paginator.paginate(
            Namespace="AWS/Bedrock",
            MetricName="Invocations",
        ):
            for metric in page.get("Metrics", []):
                for dim in metric.get("Dimensions", []):
                    if dim["Name"] == "ModelId" and dim["Value"]:
                        model_ids.add(dim["Value"])

        if not model_ids:
            return f"No Bedrock model metrics found in {region}. Either no models have been used or metrics are not available."

        # Step 2: Get invocation counts for each model in the time window
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours_back)

        model_usage = []
        for model_id in model_ids:
            resp = cw.get_metric_statistics(
                Namespace="AWS/Bedrock",
                MetricName="Invocations",
                Dimensions=[{"Name": "ModelId", "Value": model_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=hours_back * 3600,  # Single period for total count
                Statistics=["Sum"],
            )
            datapoints = resp.get("Datapoints", [])
            if datapoints:
                total = sum(dp["Sum"] for dp in datapoints)
                if total > 0:
                    # Resolve friendly name
                    clean_id = model_id
                    for prefix in ("us.", "eu.", "ap.", "jp.", "global."):
                        if clean_id.startswith(prefix):
                            clean_id = clean_id[len(prefix):]
                            break
                    info = get_model_info(clean_id)
                    display = info["name"] if info else model_id
                    model_usage.append({
                        "model_id": model_id,
                        "display_name": display,
                        "invocations": total,
                    })

        if not model_usage:
            return f"No Bedrock model invocations found in {region} in the past {hours_back} hours."

        # Step 3: Sort by invocation count descending
        model_usage.sort(key=lambda x: x["invocations"], reverse=True)

        result = [f"Active Bedrock Models in {region} (past {hours_back} hours):"]
        result.append("=" * 80)
        for i, m in enumerate(model_usage, 1):
            result.append(f"\n{i}. {m['display_name']}")
            result.append(f"   Model ID: {m['model_id']}")
            result.append(f"   Invocations: {m['invocations']:.0f}")

        result.append(f"\nTotal: {len(model_usage)} models with activity")
        return "\n".join(result)

    except Exception as e:
        logger.error(f"Error discovering active models: {e}", exc_info=True)
        return f"Error discovering active models: {str(e)}"
