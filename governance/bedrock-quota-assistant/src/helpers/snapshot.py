# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Customer Profile Snapshot — cached DynamoDB read and LLM rendering."""

import logging

import boto3

from config import ssm_region, QUOTA_CACHE_TABLE

logger = logging.getLogger(__name__)

_snapshot_cache = None


def get_snapshot_cached() -> dict | None:
    """Return the customer-profile Snapshot, reading DynamoDB at most once per turn."""
    global _snapshot_cache

    if _snapshot_cache is not None:
        return _snapshot_cache

    try:
        dynamodb = boto3.resource("dynamodb", region_name=ssm_region)
        table = dynamodb.Table(QUOTA_CACHE_TABLE)
        response = table.get_item(
            Key={"PK": "customer-profile", "SK": "latest"}
        )
    except Exception as e:
        logger.warning(f"Failed to read customer-profile Snapshot from DynamoDB: {e}")
        return None

    item = response.get("Item")
    if item is None:
        return None

    _snapshot_cache = item
    return _snapshot_cache


def _format_for_llm(snapshot: dict) -> str:
    """Transform structured snapshot into model-optimized markdown."""
    models = snapshot.get("models") or []
    assembled_at = snapshot.get("assembled_at", "unknown")
    regions = snapshot.get("regions_scanned") or []

    if not models:
        return (
            f"# Customer Profile\n\n"
            f"No active Bedrock models found.\n"
            f"Scanned regions: {', '.join(regions)}\n"
            f"Last updated: {assembled_at}"
        )

    quota_groups = []
    model_inventory = []

    group_idx = 0
    for model in models:
        display_name = model.get("display_name", model.get("model_id", "Unknown"))
        patterns_summary = []

        for pattern in model.get("active_patterns", []):
            pattern_type = pattern.get("pattern_type", "on-demand")
            geography = pattern.get("geography")
            limits = pattern.get("quota_limits", {})
            tpm_limit = limits.get("tpm_limit", -1)
            rpm_limit = limits.get("rpm_limit", -1)
            invocations = pattern.get("invocations_24h", 0)

            if pattern_type == "cross-region-geo" and geography:
                pattern_label = f"{geography}. cross-region"
            elif pattern_type == "cross-region-global":
                pattern_label = "global cross-region"
            else:
                pattern_label = "on-demand"

            wraps_key = "cross-region" if pattern_type != "on-demand" else "on-demand"
            consumers = [
                ap.get("name", "unnamed")
                for ap in model.get("app_profiles", [])
                if ap.get("wraps") == wraps_key
            ]

            if tpm_limit > 0 or rpm_limit > 0:
                group_idx += 1
                quota_groups.append({
                    "idx": group_idx,
                    "display_name": display_name,
                    "pattern_label": pattern_label,
                    "tpm_limit": tpm_limit,
                    "rpm_limit": rpm_limit,
                    "invocations_24h": invocations,
                    "consumers": consumers,
                })
                patterns_summary.append((pattern_label, group_idx, invocations))
            else:
                patterns_summary.append((pattern_label, None, invocations))

        model_inventory.append((display_name, patterns_summary))

    lines = [f"# Customer Profile (updated {assembled_at[:10]})"]
    lines.append(f"Regions: {', '.join(regions)}")
    lines.append("")

    if quota_groups:
        lines.append("## Quota Groups")
        for qg in quota_groups:
            tpm_str = f"{int(qg['tpm_limit']):,} TPM limit" if qg["tpm_limit"] > 0 else "TPM unknown"
            rpm_str = f"{int(qg['rpm_limit']):,} RPM limit" if qg["rpm_limit"] > 0 else "RPM unknown"
            inv_str = f"{qg['invocations_24h']:,} invocations/24h" if qg["invocations_24h"] > 0 else "no recent usage"
            lines.append(
                f"- [Q{qg['idx']}] {qg['pattern_label']} {qg['display_name']}: "
                f"{tpm_str}, {rpm_str} ({inv_str})"
            )
            if qg["consumers"]:
                lines.append(f"  Consumers: {', '.join(qg['consumers'])}")
        lines.append("")

    lines.append("## Model Inventory")
    for display_name, patterns in model_inventory:
        parts = []
        for pattern_label, group_idx, invocations in patterns:
            activity = "active" if invocations > 0 else "inactive"
            ref = f" → Q{group_idx}" if group_idx else ""
            parts.append(f"{pattern_label} ({activity}{ref})")
        lines.append(f"- {display_name}: {', '.join(parts)}")

    all_profiles = []
    for model in models:
        for ap in model.get("app_profiles", []):
            all_profiles.append((
                ap.get("name", "unnamed"),
                model.get("display_name", "unknown"),
                ap.get("wraps", "unknown"),
                ap.get("has_cw_data", False),
            ))

    if all_profiles:
        lines.append("")
        lines.append("## Application Profiles")
        for name, base_model, wraps, has_data in all_profiles:
            status = "active" if has_data else "no recent data"
            lines.append(f"- {name} → {base_model} ({wraps}, {status})")

    return "\n".join(lines)
