# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: list_active_inference_profiles — per-profile discovery and ranking."""

import logging
from datetime import datetime, timedelta

import boto3
from strands import tool

from config import DEFAULT_REGION
from helpers.profile_resolution import (
    is_active_inference_profile,
    MIN_INVOCATIONS_FOR_ACTIVE,
    _APP_PROFILE_ARN_RE,
)
from helpers.snapshot import get_snapshot_cached

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _access_pattern_label_from_pattern(pattern: dict) -> str:
    """Render the Access Pattern label for a Snapshot AccessPattern.

    Maps ``pattern_type`` + ``geography`` to the display label shown in
    Template E / F. Defaults to ``"on-demand"`` when ``pattern_type`` is
    absent or unrecognised, matching the permissive posture of the composers.
    """
    pattern_type = pattern.get("pattern_type") or "on-demand"
    if pattern_type == "cross-region-geo":
        geography = pattern.get("geography")
        if geography:
            return f"cross-region-geo ({geography})"
        return "cross-region-geo"
    if pattern_type == "cross-region-global":
        return "cross-region-global"
    return "on-demand"


def _infer_access_pattern_label_from_cw_id(cw_model_id: str) -> str:
    """Infer the Access Pattern label from a bare CW_Model_ID.

    Used only on the snapshot-miss fallback path, where inventory came from
    ``cloudwatch.list_metrics`` and the ``pattern_type`` field is not available.
    Mirrors the classification used by the customer-profile refresh Lambda.
    """
    if cw_model_id.startswith("global."):
        return "cross-region-global"
    for prefix in ("us.", "eu.", "ap.", "jp."):
        if cw_model_id.startswith(prefix):
            return f"cross-region-geo ({prefix.rstrip('.')})"
    return "on-demand"


def _fetch_profile_invocations(
    cw_client,
    cw_model_id: str,
    start_time: datetime,
    end_time: datetime,
    hours_back: int,
) -> int:
    """Sum Invocations in the window for a single CW_Model_ID.

    Uses the same ``get_metric_statistics`` call shape as
    ``list_active_bedrock_models`` so the two tools report identical numbers
    for the same profile. Returns 0 when CloudWatch has no datapoints.
    """
    resp = cw_client.get_metric_statistics(
        Namespace="AWS/Bedrock",
        MetricName="Invocations",
        Dimensions=[{"Name": "ModelId", "Value": cw_model_id}],
        StartTime=start_time,
        EndTime=end_time,
        Period=hours_back * 3600,  # single period = total over window
        Statistics=["Sum"],
    )
    datapoints = resp.get("Datapoints", [])
    total = 0
    for dp in datapoints:
        total += int(dp.get("Sum") or 0)
    return total


def _list_active_inference_profiles_impl(
    region: str,
    hours_back: int,
    show_all_system_profiles: bool,
) -> str:
    """Profile-grain discovery renderer shared by two ``@tool`` entry points.

    This is the actual body that produces Templates E and F. It is invoked by:

      - ``list_active_inference_profiles`` (the dedicated profile-grain tool), and
      - ``list_active_bedrock_models`` with ``by_profile=True`` (Tasks 6.1-6.2).

    Factoring the body out keeps the two tools in lockstep byte-for-byte
    (Req 3.10) — the ``by_profile=True`` branch of ``list_active_bedrock_models``
    cannot silently drift from ``list_active_inference_profiles`` because both
    call this single implementation.
    """
    try:
        cw = boto3.client("cloudwatch", region_name=region)
        snapshot = get_snapshot_cached()

        # Each candidate dict carries what we need to filter, sort, and render.
        # ``invocations`` is filled in below after CloudWatch lookup.
        candidates: list[dict] = []

        if snapshot is not None:
            # Snapshot present — inventory comes from models[*].active_patterns
            # (System_Defined_Profiles) and models[*].app_profiles (App_Profiles).
            for model_entry in snapshot.get("models") or []:
                for pattern in model_entry.get("active_patterns") or []:
                    cw_model_id = pattern.get("cw_model_id")
                    if not cw_model_id:
                        continue
                    candidates.append({
                        "kind": "system_profile",
                        "cw_model_id": cw_model_id,
                        "access_pattern_label": (
                            _access_pattern_label_from_pattern(pattern)
                        ),
                        "application_profile_name": None,
                        "application_profile_arn": None,
                    })
                for app_profile in model_entry.get("app_profiles") or []:
                    arn = app_profile.get("arn")
                    if not arn:
                        continue
                    candidates.append({
                        "kind": "application_profile",
                        "cw_model_id": arn,
                        "access_pattern_label": "application-profile",
                        "application_profile_name": app_profile.get("name"),
                        "application_profile_arn": arn,
                    })
        else:
            # Snapshot miss — fall back to live list_metrics discovery, the
            # same pattern used by list_active_bedrock_models above. Classify
            # each discovered ID as an App_Profile (ARN regex) or a
            # System_Defined_Profile (prefix or bare), and infer the Access
            # Pattern label from the ID shape.
            paginator = cw.get_paginator("list_metrics")
            discovered: set[str] = set()
            for page in paginator.paginate(
                Namespace="AWS/Bedrock",
                MetricName="Invocations",
            ):
                for metric in page.get("Metrics", []):
                    for dim in metric.get("Dimensions", []):
                        if dim.get("Name") == "ModelId" and dim.get("Value"):
                            discovered.add(dim["Value"])

            for mid in discovered:
                if _APP_PROFILE_ARN_RE.match(mid):
                    candidates.append({
                        "kind": "application_profile",
                        "cw_model_id": mid,
                        "access_pattern_label": "application-profile",
                        "application_profile_name": None,
                        "application_profile_arn": mid,
                    })
                else:
                    candidates.append({
                        "kind": "system_profile",
                        "cw_model_id": mid,
                        "access_pattern_label": (
                            _infer_access_pattern_label_from_cw_id(mid)
                        ),
                        "application_profile_name": None,
                        "application_profile_arn": None,
                    })

        # Fetch per-candidate invocations over the window.
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours_back)
        for c in candidates:
            c["invocations"] = _fetch_profile_invocations(
                cw, c["cw_model_id"], start_time, end_time, hours_back
            )

        # Apply the asymmetric inclusion rule.
        filtered = [
            c
            for c in candidates
            if is_active_inference_profile(
                c["kind"], c["invocations"], show_all_system_profiles
            )
        ]

        if not filtered:
            return (
                f"No Bedrock inference profiles found in {region} "
                f"in the past {hours_back} hours."
            )

        # Sort tiers:
        #   0) profiles with invocations > 0, descending by invocations;
        #   1) zero-invocation Application_Profiles, alphabetical by name;
        #   2) inactive System_Defined_Profiles (only when show_all is True),
        #      alphabetical by cw_model_id.
        def _sort_key(c: dict) -> tuple[int, int, str]:
            invocations = c["invocations"]
            if invocations > 0:
                return (0, -invocations, "")
            if c["kind"] == "application_profile":
                name = (
                    c.get("application_profile_name")
                    or c.get("application_profile_arn")
                    or ""
                )
                return (1, 0, name.lower())
            return (2, 0, (c.get("cw_model_id") or "").lower())

        filtered.sort(key=_sort_key)

        # --- Render -------------------------------------------------------
        lines: list[str] = []
        if show_all_system_profiles:
            lines.append(
                f"Active Inference Profiles in {region} "
                f"(past {hours_back} hours) — showing all system profiles:"
            )
        else:
            lines.append(
                f"Active Inference Profiles in {region} "
                f"(past {hours_back} hours):"
            )
        lines.append("=" * 80)

        active_count = 0
        inactive_system_count = 0
        no_recent_usage_app_count = 0

        for idx, c in enumerate(filtered, 1):
            is_app = c["kind"] == "application_profile"
            invocations = c["invocations"]
            below_threshold = invocations < MIN_INVOCATIONS_FOR_ACTIVE

            if is_app:
                display = (
                    c.get("application_profile_name")
                    or c.get("application_profile_arn")
                    or c["cw_model_id"]
                )
            else:
                display = c["cw_model_id"]

            tags: list[str] = []
            if is_app:
                tags.append("(application-profile)")
                if below_threshold:
                    tags.append("(no recent usage)")
            else:
                if show_all_system_profiles and below_threshold:
                    tags.append("(inactive — available but unused)")

            header_line = f"{idx}. {display}"
            for tag in tags:
                header_line += f"  {tag}"

            lines.append("")
            lines.append(header_line)

            if is_app:
                arn = c.get("application_profile_arn") or c["cw_model_id"]
                lines.append(f"   ARN: {arn}")
            else:
                lines.append(
                    f"   Access Pattern: {c['access_pattern_label']}"
                )
            lines.append(f"   Invocations: {invocations:,}")

            if invocations > 0:
                active_count += 1
            elif is_app:
                no_recent_usage_app_count += 1
            else:
                inactive_system_count += 1

        # Footer — Template E vs Template F phrasing.
        total = len(filtered)
        lines.append("")
        if show_all_system_profiles:
            inactive_word = (
                "inactive system profile"
                if inactive_system_count == 1
                else "inactive system profiles"
            )
            lines.append(
                f"Total: {total} profiles "
                f"({active_count} active, "
                f"{inactive_system_count} {inactive_word})"
            )
        else:
            parts = [f"{active_count} with recent activity"]
            if no_recent_usage_app_count:
                noun = (
                    "application-profile"
                    if no_recent_usage_app_count == 1
                    else "application-profiles"
                )
                parts.append(
                    f"{no_recent_usage_app_count} {noun} "
                    f"with no recent usage"
                )
            lines.append(
                f"Total: {total} profiles ({', '.join(parts)})"
            )

        return "\n".join(lines)

    except Exception as e:
        logger.error(
            f"Error listing active inference profiles: {e}", exc_info=True
        )
        return f"Error listing active inference profiles: {str(e)}"


@tool
def list_active_inference_profiles(
    region: str = DEFAULT_REGION,
    hours_back: int = 24,
    show_all_system_profiles: bool = False,
) -> str:
    """List the inference profiles that are actually in use in the account.

    Use this ONLY when the user asks for a per-profile view, names an
    Application_Profile (e.g. "marketing-bot"), or asks which profiles exist on
    a model. For "which model have I used most?" questions, use
    list_active_bedrock_models instead — that tool remains the default for
    model-centric asks.

    The asymmetric inclusion rule:
      - A System_Defined_Profile (us./eu./ap./jp./global. prefix) appears only
        if it has at least 5 invocations in the Lookback_Window.
      - An Application_Profile (application-inference-profile ARN) always
        appears, because the customer created it deliberately. If it has fewer
        than 5 invocations in the window, the entry is labelled "(no recent
        usage)" inline next to the section header.

    Set show_all_system_profiles=True ONLY when the user explicitly asks to see
    "all", "inactive", "every", or "available" profiles. The default keeps the
    list noise-free.

    Args:
        region: AWS region to query (default: agent's deployed region).
        hours_back: Lookback window in hours (default: 24).
        show_all_system_profiles: When True, also show System_Defined_Profiles
            with < 5 invocations, labelled "inactive (available but unused)".

    Returns:
        Ranked list of profiles with CW_Model_ID, Access_Pattern label,
        Application_Profile name (when applicable), and invocation count.
    """
    return _list_active_inference_profiles_impl(
        region=region,
        hours_back=hours_back,
        show_all_system_profiles=show_all_system_profiles,
    )
