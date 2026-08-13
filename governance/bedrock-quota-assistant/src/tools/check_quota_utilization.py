# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: check_quota_utilization — checks current usage against quota limits."""

import re
import logging
from datetime import datetime, timedelta

from strands import tool

from config import DEFAULT_REGION
from helpers.profile_resolution import resolve_profile_ref
from helpers.response_composer import (
    build_multi_profile_disclosure_line,
    compose_no_usage_found_message,
    compose_shared_quota_warning,
    _find_model_entry,
)
from helpers.quota_cache import (
    _query_quota_codes,
    _fetch_live_quota_values,
    _filter_strict_model_match,
)
from helpers.snapshot import get_snapshot_cached
from models import get_model_info
from tools.get_bedrock_model_invocation_metrics import (
    _get_metrics_batch,
    _resolve_cw_model_id,
    _calculate_period,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _friendly_name_from_model_id(model_id: str) -> str:
    """Derive a friendly name from a Bedrock model ID for quota lookup.

    Used as a fallback when the model is not in the catalog. Produces a
    string suitable for DynamoDB .contains() matching against quota names.

    Examples:
        'anthropic.claude-sonnet-4-6'          -> 'claude sonnet 4.6'
        'anthropic.claude-sonnet-4-5-20250929-v1:0' -> 'claude sonnet 4.5'
        'amazon.nova-pro-v1:0'                 -> 'nova pro'
        'anthropic.claude-sonnet-5'            -> 'claude sonnet 5'
        'meta.llama4-scout-17b-instruct-v1:0'  -> 'llama4 scout 17b'
    """
    # Strip provider prefix (everything before the first dot)
    name = model_id.split(".", 1)[-1]

    # Strip version suffix (-v1:0, -v2:0, etc.)
    name = re.sub(r'-v\d+:\d+$', '', name)
    # Also strip short version suffix (-v1, -v2) without colon
    name = re.sub(r'-v\d+$', '', name)

    # Strip date stamps (8-digit sequences like 20250929)
    name = re.sub(r'-\d{8}', '', name)

    # Strip 'instruct' suffix (not in quota names)
    name = re.sub(r'-instruct$', '', name, flags=re.IGNORECASE)

    # Convert hyphens to spaces
    name = name.replace("-", " ")

    # Convert version-like sequences: "4 6" -> "4.6", "3 5" -> "3.5"
    name = re.sub(r'(\d) (\d)(?=\s|$)', r'\1.\2', name)

    return name.strip()


def _fetch_rpm_tpm_quotas_for_utilization(
    region: str,
    display_name: str,
    *,
    prefer_cross_region: bool,
) -> tuple:
    """Fetch RPM/TPM quotas for ``check_quota_utilization``, biased by scope.

    Queries quota codes via ``_query_quota_codes``, fetches live values via
    ``_fetch_live_quota_values``, and applies the strict sub-version filter
    from ``_filter_strict_model_match``. Collects cross-region and on-demand
    quotas separately, then returns whichever matches the caller's preferred
    scope — falling back to the other scope when the preferred values are
    absent.

    Args:
        region: AWS region to query Service Quotas in.
        display_name: Friendly model name used to look up quota codes
            (e.g. ``"Claude Sonnet 4.6"``).
        prefer_cross_region: When ``True``, return cross-region RPM/TPM when
            available (falling back to on-demand). When ``False``, return
            on-demand RPM/TPM (falling back to cross-region).

    Returns:
        ``(rpm_quota, tpm_quota, quota_source)``. All three values are
        ``None`` when quota lookup fails (DynamoDB cache miss, no matching
        quotas, or any underlying exception) — matching the prior
        inline-in-``check_quota_utilization`` fallback behaviour.
    """
    try:
        quota_codes = _query_quota_codes(display_name)
        if not quota_codes:
            return (None, None, None)

        values = _fetch_live_quota_values(region, quota_codes)

        # Strict filter: exclude sub-version matches (e.g., "sonnet 4.5" when
        # the caller asked about "sonnet 4").
        values = _filter_strict_model_match(values, display_name)

        cr_rpm = cr_tpm = od_rpm = od_tpm = None
        cr_source = od_source = None
        for q in values:
            if q["value"] == "THROTTLED":
                continue
            name_lower = q["name"].lower()
            if "cross-region" in name_lower:
                if "requests per minute" in name_lower and cr_rpm is None:
                    cr_rpm = q["value"]
                    cr_source = q["name"]
                elif "tokens per minute" in name_lower and cr_tpm is None:
                    cr_tpm = q["value"]
            else:
                if "requests per minute" in name_lower and od_rpm is None:
                    od_rpm = q["value"]
                    od_source = q["name"]
                elif "tokens per minute" in name_lower and od_tpm is None:
                    od_tpm = q["value"]

        if prefer_cross_region:
            rpm_quota = cr_rpm if cr_rpm is not None else od_rpm
            tpm_quota = cr_tpm if cr_tpm is not None else od_tpm
            quota_source = cr_source or od_source
        else:
            rpm_quota = od_rpm if od_rpm is not None else cr_rpm
            tpm_quota = od_tpm if od_tpm is not None else cr_tpm
            quota_source = od_source or cr_source

        return (rpm_quota, tpm_quota, quota_source)

    except Exception as e:
        logger.warning(f"Could not fetch quotas for utilization calc: {e}")
        return (None, None, None)


def _find_underlying_cw_model_id_for_app_profile(
    resolved: dict,
    snapshot: dict | None,
) -> tuple:
    """Return ``(underlying_cw_model_id, pattern_type_label)`` for an App_Profile.

    Application_Profiles emit CloudWatch metrics dimensioned by their ARN,
    but Template D's ``Underlying CW_Model_ID`` line (and the
    ``shared with other callers of ...`` label) both need the CW_Model_ID of
    the *wrapped* target (the base model for on-demand wraps, the matching
    System_Defined_Profile for cross-region wraps).

    Resolution: look up the ModelEntry for the resolved profile's
    ``base_model_id``; find the first ``active_pattern`` whose
    ``pattern_type`` aligns with ``underlying_quota_scope``; return its
    ``cw_model_id`` plus the ``pattern_type`` string (used verbatim as the
    access-pattern label). Falls back to the bare ``base_model_id`` with
    label ``"on-demand"`` when scope is ``"on-demand"`` and no on-demand
    active_pattern is in the Snapshot (the bare model ID *is* the
    CW_Model_ID for direct on-demand usage).

    Returns ``(None, None)`` when identification is not possible (no
    snapshot, missing ``base_model_id``, unknown scope, or missing
    cross-region active_pattern for cross-region scope).
    """
    if resolved is None or snapshot is None:
        return (None, None)
    scope = resolved.get("underlying_quota_scope")
    base_model_id = resolved.get("base_model_id")
    if not base_model_id:
        return (None, None)

    if scope == "cross-region":
        allowed_pattern_types = ("cross-region-geo", "cross-region-global")
    elif scope == "on-demand":
        allowed_pattern_types = ("on-demand",)
    else:
        return (None, None)

    model_entry = _find_model_entry(snapshot, base_model_id)
    if model_entry is not None:
        for pattern in model_entry.get("active_patterns") or []:
            if pattern.get("pattern_type") not in allowed_pattern_types:
                continue
            cw_model_id = pattern.get("cw_model_id")
            if cw_model_id:
                return (cw_model_id, pattern.get("pattern_type"))

    # Reasonable fallback: direct on-demand traffic is keyed on the bare
    # base model ID, so we can still render Template D when the Snapshot
    # doesn't carry an explicit on-demand active_pattern.
    if scope == "on-demand":
        return (base_model_id, "on-demand")
    return (None, None)


def _append_metrics_and_risk_sections(
    result: list,
    metrics: dict,
    rpm_quota,
    tpm_quota,
    alerts: list,
    *,
    per_app: bool = False,
    underlying_cw_model_id: str | None = None,
) -> None:
    """Render RPM, TPM, latency, error/throttle, and risk lines into ``result``.

    Two variants, selected by ``per_app``:

    - ``per_app=False`` (model-grain, System_Defined_Profile) —
      ``Requests Per Minute (RPM):``, ``Peak Utilization:`` /
      ``Average Utilization:``, ``Quota Limit: N RPM`` (plain).
    - ``per_app=True`` (Application_Profile, Template D) —
      ``Requests Per Minute (RPM) — this application alone:``,
      ``Quota Limit: N RPM (shared with other callers of {underlying})``,
      ``Peak Utilization (this app):``.

    When ``per_app=True`` and ``rpm_quota``/``tpm_quota`` are ``None``
    (Task 7.4 — ``underlying_quota_scope=None`` branch), the quota and
    utilization lines are suppressed so the caller can emit raw metrics
    without a percentage (Req 5.5).

    Alerts (``WARNING`` / ``CRITICAL``) are appended to ``alerts`` so the
    caller can render the shared risk-assessment block below the body.
    """
    suffix = " — this application alone" if per_app else ""

    # RPM
    inv = metrics.get("invocations") or []
    if inv:
        max_rpm = max(v for _, v in inv)
        avg_rpm = sum(v for _, v in inv) / len(inv)
        peak_ts = max(inv, key=lambda x: x[1])[0]
        result.append(f"\nRequests Per Minute (RPM){suffix}:")
        result.append(f"  Peak: {max_rpm:.0f} (at {peak_ts.isoformat()}Z)")
        result.append(f"  Average: {avg_rpm:.2f}")
        if rpm_quota and rpm_quota > 0:
            peak_rpm_pct = (max_rpm / rpm_quota) * 100
            avg_rpm_pct = (avg_rpm / rpm_quota) * 100
            if per_app:
                if underlying_cw_model_id:
                    result.append(
                        f"  Quota Limit: {rpm_quota:.0f} RPM "
                        f"(shared with other callers of "
                        f"{underlying_cw_model_id})"
                    )
                else:
                    result.append(f"  Quota Limit: {rpm_quota:.0f} RPM (shared)")
                result.append(
                    f"  Peak Utilization (this app): {peak_rpm_pct:.1f}%"
                )
            else:
                result.append(f"  Quota Limit: {rpm_quota:.0f} RPM")
                result.append(f"  Peak Utilization: {peak_rpm_pct:.1f}%")
                result.append(f"  Average Utilization: {avg_rpm_pct:.1f}%")
            if peak_rpm_pct >= 90:
                alerts.append(
                    f"CRITICAL: RPM at {peak_rpm_pct:.0f}% of quota — "
                    f"actively hitting limits"
                )
            elif peak_rpm_pct >= 80:
                alerts.append(
                    f"WARNING: RPM at {peak_rpm_pct:.0f}% of quota — "
                    f"approaching limit"
                )
        elif not per_app:
            result.append(
                "  Quota Limit: Not available (could not fetch RPM quota)"
            )
        # When per_app=True and rpm_quota is None, suppress the Quota
        # Limit / Peak Utilization lines entirely — Task 7.4 path.
    else:
        result.append("\nNo invocation data found for this time period.")

    # TPM
    inp = metrics.get("input_tokens") or []
    out = metrics.get("output_tokens") or []
    if inp or out:
        result.append(f"\nTokens Per Minute (TPM){suffix}:")
        max_input_tpm = max(v for _, v in inp) if inp else 0
        max_output_tpm = max(v for _, v in out) if out else 0
        max_total_tpm = max_input_tpm + max_output_tpm
        if inp:
            result.append(f"  Peak Input: {max_input_tpm:.0f}")
        if out:
            result.append(f"  Peak Output: {max_output_tpm:.0f}")
        result.append(f"  Peak Total: {max_total_tpm:.0f}")
        if tpm_quota and tpm_quota > 0:
            peak_tpm_pct = (max_total_tpm / tpm_quota) * 100
            if per_app:
                result.append(f"  Quota Limit: {tpm_quota:.0f} TPM (shared)")
                result.append(
                    f"  Peak Utilization (this app): {peak_tpm_pct:.1f}%"
                )
            else:
                result.append(f"  Quota Limit: {tpm_quota:.0f} TPM")
                result.append(f"  Peak Utilization: {peak_tpm_pct:.1f}%")
            if peak_tpm_pct >= 90:
                alerts.append(
                    f"CRITICAL: TPM at {peak_tpm_pct:.0f}% of quota — "
                    f"actively hitting limits"
                )
            elif peak_tpm_pct >= 80:
                alerts.append(
                    f"WARNING: TPM at {peak_tpm_pct:.0f}% of quota — "
                    f"approaching limit"
                )
        elif not per_app:
            result.append(
                "  Quota Limit: Not available (could not fetch TPM quota)"
            )
    else:
        result.append(
            "\nToken metrics not available in CloudWatch for this time period."
        )

    # Latency
    lat = metrics.get("latency") or []
    if lat:
        avg_latency = sum(v for _, v in lat) / len(lat)
        peak_latency = max(v for _, v in lat)
        result.append("\nInvocation Latency:")
        result.append(f"  Average: {avg_latency:.0f}ms")
        result.append(f"  Peak: {peak_latency:.0f}ms")

    # Errors + throttles
    th = metrics.get("throttles") or []
    total_throttles = sum(v for _, v in th) if th else 0
    ce = metrics.get("client_errors") or []
    total_client_errors = sum(v for _, v in ce) if ce else 0
    se = metrics.get("server_errors") or []
    total_server_errors = sum(v for _, v in se) if se else 0

    result.append(f"\nThrottled Requests: {total_throttles:.0f}")
    result.append(f"Client Errors: {total_client_errors:.0f}")
    result.append(f"Server Errors: {total_server_errors:.0f}")

    if total_throttles > 0 and inv:
        total_invocations = sum(v for _, v in inv)
        if total_invocations > 0:
            throttle_rate = (total_throttles / total_invocations) * 100
            result.append(f"Throttle Rate: {throttle_rate:.2f}%")
            if throttle_rate >= 5:
                alerts.append(
                    f"WARNING: {throttle_rate:.1f}% of requests are being "
                    f"throttled"
                )


def _append_risk_and_tip(
    result: list,
    metrics: dict,
    rpm_quota,
    tpm_quota,
    alerts: list,
    period: int,
) -> None:
    """Append the risk-assessment block and peak-drill-down tip."""
    if alerts:
        result.append(f"\n{'!' * 60}")
        result.append("RISK ASSESSMENT:")
        for alert in alerts:
            result.append(f"  ⚠ {alert}")
        result.append(
            "\nRecommendation: Consider preparing a quota increase request "
            "via draft_quota_increase_request."
        )
        result.append(f"{'!' * 60}")
    elif rpm_quota or tpm_quota:
        result.append("\n✅ Utilization is within safe limits.")

    inv = metrics.get("invocations") or []
    if period > 60 and inv:
        peak_ts = max(inv, key=lambda x: x[1])[0]
        result.append(
            f"\nTip: For minute-level detail around the peak, query with "
            f"start_time_iso around {peak_ts.isoformat()}Z"
        )


def _render_model_grain_utilization(
    resolved: dict,
    snapshot: dict | None,
    region: str,
    start_time,
    end_time,
    period: int,
    granularity: str,
) -> str:
    """Render the model-grain utilization output (Tasks 7.1, 7.5, 7.6).

    Preserves the existing ``check_quota_utilization`` output shape for bare
    model IDs and friendly aliases. When the Snapshot shows 2+ Active
    Inference_Profiles with invocations > 0 on this base model, a
    disclosure line is appended at the end (Req 1.2 / Task 7.5).
    """
    resolved_model_id = (
        resolved.get("base_model_id")
        or resolved.get("cw_model_id")
        or ""
    )
    model_info = get_model_info(resolved_model_id)
    display_name = model_info["name"] if model_info else _friendly_name_from_model_id(resolved_model_id)

    # Region-aware CloudWatch model ID (may prepend eu./ap. for regional
    # cross-region inference).
    cw_model_id, profile_type = _resolve_cw_model_id(resolved_model_id, region)

    metrics = _get_metrics_batch(
        region, cw_model_id, start_time, end_time, period
    )
    has_data = any(len(v) > 0 for v in metrics.values())
    if (
        not has_data
        and profile_type == "UNKNOWN"
        and not cw_model_id.startswith("us.")
    ):
        cw_model_id = f"us.{resolved_model_id}"
        metrics = _get_metrics_batch(
            region, cw_model_id, start_time, end_time, period
        )

    is_cross_region = (
        profile_type == "CROSS_REGION"
        or cw_model_id.startswith("us.")
        or cw_model_id.startswith("eu.")
        or cw_model_id.startswith("ap.")
        or cw_model_id.startswith("global.")
    )

    rpm_quota, tpm_quota, _ = _fetch_rpm_tpm_quotas_for_utilization(
        region, display_name, prefer_cross_region=is_cross_region
    )

    result: list = []
    result.append(f"Quota Utilization Analysis for {display_name}")
    result.append(f"Model ID: {resolved_model_id}")
    if cw_model_id != resolved_model_id:
        result.append(
            f"CloudWatch Model ID: {cw_model_id} "
            f"({profile_type.lower().replace('_', '-')})"
        )
    result.append(f"Region: {region}")
    result.append(
        f"Inference Type: "
        f"{'Cross-region' if is_cross_region else 'On-demand (single region)'}"
    )
    result.append(f"Granularity: {granularity} averages ({period}s period)")
    result.append("=" * 80)

    alerts: list = []
    _append_metrics_and_risk_sections(
        result, metrics, rpm_quota, tpm_quota, alerts, per_app=False
    )
    _append_risk_and_tip(result, metrics, rpm_quota, tpm_quota, alerts, period)

    # Task 7.5 — multi-profile disclosure line when the Snapshot shows 2+
    # active profiles on this base model.
    disclosure = build_multi_profile_disclosure_line(resolved_model_id, snapshot)
    if disclosure:
        result.append(f"\n{disclosure}")

    return "\n".join(result)


def _render_system_profile_utilization(
    resolved: dict,
    region: str,
    start_time,
    end_time,
    period: int,
    granularity: str,
    hours_back: int,
) -> str:
    """Render utilization for a System_Defined_Profile ref (Task 7.2).

    Uses the resolved ``cw_model_id`` (e.g. ``us.anthropic.claude-...``)
    verbatim for both CloudWatch and the output header, fetches the
    cross-region Underlying_Quota via
    ``_fetch_rpm_tpm_quotas_for_utilization(prefer_cross_region=True)``,
    and renders model-grain output with ``Inference Type: Cross-region``.
    """
    cw_model_id = resolved.get("cw_model_id") or ""
    base_model_id = resolved.get("base_model_id") or cw_model_id
    model_info = get_model_info(base_model_id)
    display_name = model_info["name"] if model_info else _friendly_name_from_model_id(base_model_id)

    metrics = _get_metrics_batch(
        region, cw_model_id, start_time, end_time, period
    )

    # Task 7.6 — empty window: emit no-usage-found message naming the profile
    # that was checked.
    has_data = any(len(v) > 0 for v in metrics.values())
    if not has_data:
        return compose_no_usage_found_message(
            display_name, region, hours_back, [cw_model_id]
        )

    rpm_quota, tpm_quota, _ = _fetch_rpm_tpm_quotas_for_utilization(
        region, display_name, prefer_cross_region=True
    )

    result: list = []
    result.append(f"Quota Utilization Analysis for {display_name}")
    result.append(f"Model ID: {cw_model_id}")
    result.append(f"Region: {region}")
    result.append("Inference Type: Cross-region")
    result.append(f"Granularity: {granularity} averages ({period}s period)")
    result.append("=" * 80)

    alerts: list = []
    _append_metrics_and_risk_sections(
        result, metrics, rpm_quota, tpm_quota, alerts, per_app=False
    )
    _append_risk_and_tip(result, metrics, rpm_quota, tpm_quota, alerts, period)

    return "\n".join(result)


def _render_app_profile_utilization(
    resolved: dict,
    snapshot: dict | None,
    region: str,
    start_time,
    end_time,
    period: int,
    granularity: str,
    hours_back: int,
) -> str:
    """Render utilization for an Application_Profile (Tasks 7.3, 7.4, 7.6).

    Two sub-paths:

    - ``underlying_quota_scope`` is set (Task 7.3): render Template D — per-app
      alone phrasing, ``(shared with other callers of <underlying>)`` label,
      and the shared-quota warning block via ``compose_shared_quota_warning``.
    - ``underlying_quota_scope`` is ``None`` (Task 7.4, Req 5.5): return raw
      metrics without percentages and explain the underlying quota could not
      be resolved. Does NOT call ``_fetch_rpm_tpm_quotas_for_utilization`` —
      we don't have a well-defined scope to fetch for.
    """
    cw_model_id = resolved.get("cw_model_id") or ""
    arn = resolved.get("application_profile_arn") or cw_model_id
    name = resolved.get("application_profile_name") or arn
    scope = resolved.get("underlying_quota_scope")

    metrics = _get_metrics_batch(
        region, cw_model_id, start_time, end_time, period
    )

    # Task 7.6 — empty-window message naming the profile that was checked.
    has_data = any(len(v) > 0 for v in metrics.values())
    if not has_data:
        return compose_no_usage_found_message(
            name, region, hours_back, [name]
        )

    # --- Task 7.4 — underlying quota could not be resolved ------------------
    if scope is None:
        result: list = []
        result.append(f"Quota Utilization Analysis for {name}")
        result.append(f"Application_Profile ARN: {arn}")
        result.append(f"Region: {region}")
        result.append(f"Granularity: {granularity} averages ({period}s period)")
        result.append("=" * 80)
        result.append(
            "\nNote: The underlying quota for this application profile "
            "could not be resolved, so utilization percentages are not "
            "shown. Raw usage metrics are reported below."
        )
        alerts: list = []
        _append_metrics_and_risk_sections(
            result, metrics, None, None, alerts, per_app=False
        )
        # No risk-assessment block (no quota to compare against), but keep
        # the peak-window drill-down tip for long lookbacks.
        _append_risk_and_tip(result, metrics, None, None, alerts, period)
        return "\n".join(result)

    # --- Task 7.3 — Template D with shared-quota warning ---------------------
    underlying_cw_model_id, pattern_label = (
        _find_underlying_cw_model_id_for_app_profile(resolved, snapshot)
    )

    prefer_cross_region = (scope == "cross-region")
    rpm_quota, tpm_quota, _ = _fetch_rpm_tpm_quotas_for_utilization(
        region, name, prefer_cross_region=prefer_cross_region
    )
    # Fall back to the base_model_id's friendly display name for the
    # Service Quotas lookup when the application-profile name is not a
    # recognised quota-name fragment.
    if rpm_quota is None and tpm_quota is None:
        base_model_id = resolved.get("base_model_id")
        model_info = get_model_info(base_model_id) if base_model_id else None
        if model_info:
            rpm_quota, tpm_quota, _ = _fetch_rpm_tpm_quotas_for_utilization(
                region,
                model_info.get("name") or base_model_id,
                prefer_cross_region=prefer_cross_region,
            )

    inference_type_phrase = (
        "Cross-region (shared quota)"
        if scope == "cross-region"
        else "On-demand (shared quota)"
    )

    result = []
    result.append(f"Quota Utilization Analysis for {name}")
    result.append(f"Application_Profile ARN: {arn}")
    if underlying_cw_model_id and pattern_label:
        result.append(
            f"Underlying CW_Model_ID: {underlying_cw_model_id} "
            f"({pattern_label})"
        )
    elif underlying_cw_model_id:
        result.append(f"Underlying CW_Model_ID: {underlying_cw_model_id}")
    result.append(f"Region: {region}")
    result.append(f"Inference Type: {inference_type_phrase}")
    result.append(f"Granularity: {granularity} averages ({period}s period)")
    result.append("=" * 80)

    alerts = []
    _append_metrics_and_risk_sections(
        result,
        metrics,
        rpm_quota,
        tpm_quota,
        alerts,
        per_app=True,
        underlying_cw_model_id=underlying_cw_model_id,
    )

    # Shared-quota warning block — lists every other caller of the same
    # Underlying_Quota (Req 5.4 / Template D).
    warning = compose_shared_quota_warning(resolved, snapshot)
    if warning:
        result.append("")
        result.append(warning)

    _append_risk_and_tip(result, metrics, rpm_quota, tpm_quota, alerts, period)

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------


@tool
def check_quota_utilization(
    model_id: str,
    region: str = DEFAULT_REGION,
    hours_back: int = 1,
    start_time_iso: str = None,
    end_time_iso: str = None,
) -> str:
    """Check current utilization against quotas for a model or inference profile.

    Accepts any Profile_Ref:
      - bare model ID or friendly alias (model grain, e.g. "claude sonnet 4.6")
      - System_Defined_Profile ID (e.g. "us.anthropic.claude-sonnet-4-6-v1:0")
      - Application_Profile ARN
      - Application_Profile friendly name (e.g. "marketing-bot")
      - friendly alias + profile-family keyword (e.g. "claude sonnet 4.6 global")

    Application_Profiles share their Underlying_Quota with every other caller
    of the same foundation model or System_Defined_Profile — the response
    labels the reported quota as shared and warns when other active traffic
    is visible.

    If a bare model ID is passed and the Snapshot shows 2+ Active_Inference
    Profiles with non-zero 24h invocations for that model, the response stays
    at model grain and appends a one-line disclosure listing the active
    profiles.

    Args:
        model_id: Profile_Ref — bare ID, prefixed ID, ARN, or friendly name.
        region: AWS region (default: agent's deployed region).
        hours_back: Hours to analyze (default: 1). Ignored if start_time_iso is set.
        start_time_iso: Optional ISO-8601 start time for precise time windows.
        end_time_iso: Optional ISO-8601 end time (defaults to now).

    Returns:
        Utilization analysis with peaks, percentages, and risk flags.
    """
    try:
        # Task 7.1 — resolve the ref once, up front, so every branch below
        # has a clean ResolvedProfileRef to dispatch on.
        snapshot = get_snapshot_cached()
        resolved = resolve_profile_ref(model_id, snapshot)
        kind = resolved["kind"]

        # Task 7.7 — ambiguous ref: list candidates and stop without touching
        # CloudWatch or Service Quotas (Req 4.5).
        if kind == "ambiguous":
            candidates = resolved.get("candidates") or []
            lines = [
                f"Multiple inference profiles match '{model_id}'. Please pick one:"
            ]
            for label in candidates:
                lines.append(f"  - {label}")
            return "\n".join(lines)

        # Task 7.8 — unresolved ref: point the user at get_customer_profile
        # so they can see what references are available (Req 4.6).
        if kind == "unresolved":
            unresolved_ref = resolved.get("unresolved_ref") or model_id
            return (
                f"Could not resolve '{unresolved_ref}' to any inference "
                f"profile. Try calling get_customer_profile to see the "
                f"available inference profiles."
            )

        # Shared time-range + adaptive-period calculation for every remaining
        # branch.
        if start_time_iso:
            start_time = datetime.fromisoformat(
                start_time_iso.replace("Z", "+00:00")
            ).replace(tzinfo=None)
            if end_time_iso:
                end_time = datetime.fromisoformat(
                    end_time_iso.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            else:
                end_time = datetime.utcnow()
            actual_hours = (end_time - start_time).total_seconds() / 3600
        else:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(hours=hours_back)
            actual_hours = hours_back
        period, granularity = _calculate_period(actual_hours)

        if kind == "application_profile":
            # Tasks 7.3, 7.4, 7.6.
            return _render_app_profile_utilization(
                resolved, snapshot, region,
                start_time, end_time, period, granularity, hours_back,
            )

        if kind == "system_profile":
            # Tasks 7.2, 7.6.
            return _render_system_profile_utilization(
                resolved, region,
                start_time, end_time, period, granularity, hours_back,
            )

        # kind == "model" — Task 7.1 fall-through + Task 7.5 disclosure line.
        return _render_model_grain_utilization(
            resolved, snapshot, region,
            start_time, end_time, period, granularity,
        )

    except Exception as e:
        logger.error(f"Error analyzing quota utilization: {e}", exc_info=True)
        return f"Error analyzing quota utilization: {str(e)}"
