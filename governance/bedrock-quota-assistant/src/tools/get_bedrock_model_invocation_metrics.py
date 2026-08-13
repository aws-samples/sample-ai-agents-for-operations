# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Tool: get_bedrock_model_invocation_metrics — CloudWatch metrics for Bedrock invocations."""

import logging
from datetime import datetime, timedelta

import boto3
from strands import tool

from config import DEFAULT_REGION
from helpers.profile_resolution import resolve_profile_ref
from helpers.snapshot import get_snapshot_cached
from models import get_model_info

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_metric_with_model_id_variants(cw_client, metric_name: str, model_id: str,
                                         start_time, end_time, period: int, statistics: list) -> dict:
    """
    Try to get CloudWatch metrics with different model ID formats.
    CloudWatch may store metrics with 'us.' prefix for cross-region inference.

    DEPRECATED: Kept for backward compatibility. New code should use
    _resolve_cw_model_id() + _get_metrics_batch() instead.
    """
    # Model ID variants to try
    model_id_variants = [model_id]

    # Add cross-region prefix variants based on region
    if not any(model_id.startswith(p) for p in ('us.', 'eu.', 'ap.', 'global.')):
        # Derive regional prefix from region name
        if DEFAULT_REGION.startswith('eu-'):
            model_id_variants.insert(0, f'eu.{model_id}')
        elif DEFAULT_REGION.startswith('ap-'):
            model_id_variants.insert(0, f'ap.{model_id}')
        model_id_variants.append(f'us.{model_id}')
        model_id_variants.append(f'global.{model_id}')

    for variant in model_id_variants:
        response = cw_client.get_metric_statistics(
            Namespace='AWS/Bedrock',
            MetricName=metric_name,
            Dimensions=[{'Name': 'ModelId', 'Value': variant}],
            StartTime=start_time,
            EndTime=end_time,
            Period=period,
            Statistics=statistics
        )
        if response.get('Datapoints'):
            return response, variant

    return {'Datapoints': []}, model_id


def _resolve_cw_model_id(model_id: str, region: str) -> tuple:
    """Derive the likely CloudWatch model ID based on region.

    Cross-region inference profiles use a regional prefix (us., eu., ap.) in
    CloudWatch dimensions. This function derives the prefix from the region name.
    Callers should retry with alternative prefixes if no metrics are found.

    Args:
        model_id: Base Bedrock model ID (e.g., 'anthropic.claude-3-5-sonnet-20241022-v2:0')
        region: AWS region to look up

    Returns:
        Tuple of (cw_model_id, profile_type) where profile_type is 'UNKNOWN'
        (callers should verify by checking for actual CloudWatch data).
    """
    base_id = model_id.removeprefix("us.").removeprefix("eu.").removeprefix("ap.").removeprefix("global.")
    if region.startswith('eu-'):
        return f"eu.{base_id}", "UNKNOWN"
    elif region.startswith('ap-'):
        return f"ap.{base_id}", "UNKNOWN"
    return model_id, "UNKNOWN"


def _calculate_period(hours_back: int) -> tuple:
    """Calculate appropriate CloudWatch period to stay within datapoint limits.

    GetMetricData supports up to 100,800 datapoints per call. With 5 metrics
    queried simultaneously, that's ~20,160 per metric. We pick a period that
    keeps each metric well within limits while maximizing granularity.

    Args:
        hours_back: Number of hours in the time range

    Returns:
        Tuple of (period_seconds, granularity_label) for display
    """
    total_minutes = hours_back * 60
    if total_minutes <= 1440:       # <= 24h -> 1-minute
        return 60, "1-minute"
    elif total_minutes <= 10080:    # <= 7 days -> 5-minute
        return 300, "5-minute"
    elif total_minutes <= 43200:    # <= 30 days -> 1-hour
        return 3600, "1-hour"
    else:                           # > 30 days -> 1-day
        return 86400, "1-day"


def _get_metrics_batch(
    region: str,
    cw_model_id: str,
    start_time,
    end_time,
    period: int,
) -> dict:
    """Fetch all Bedrock metrics in a single GetMetricData call.

    Queries Invocations, InputTokenCount, OutputTokenCount,
    InvocationClientErrors, InvocationServerErrors, InvocationLatency,
    and InvocationThrottles in one API call.

    Args:
        region: AWS region
        cw_model_id: The CloudWatch model ID dimension value
        start_time: Query start time
        end_time: Query end time
        period: Aggregation period in seconds

    Returns:
        Dict mapping metric short names to lists of (timestamp, value) tuples,
        sorted by timestamp ascending. Keys:
        'invocations', 'input_tokens', 'output_tokens', 'client_errors',
        'server_errors', 'latency', 'throttles'
    """
    cw = boto3.client("cloudwatch", region_name=region)

    metric_queries = [
        {"id": "invocations",    "metric": "Invocations",             "stat": "Sum"},
        {"id": "input_tokens",   "metric": "InputTokenCount",         "stat": "Sum"},
        {"id": "output_tokens",  "metric": "OutputTokenCount",        "stat": "Sum"},
        {"id": "client_errors",  "metric": "InvocationClientErrors",  "stat": "Sum"},
        {"id": "server_errors",  "metric": "InvocationServerErrors",  "stat": "Sum"},
        {"id": "latency",        "metric": "InvocationLatency",       "stat": "Average"},
        {"id": "throttles",      "metric": "InvocationThrottles",     "stat": "Sum"},
    ]

    queries = []
    for mq in metric_queries:
        queries.append({
            "Id": mq["id"],
            "MetricStat": {
                "Metric": {
                    "Namespace": "AWS/Bedrock",
                    "MetricName": mq["metric"],
                    "Dimensions": [{"Name": "ModelId", "Value": cw_model_id}],
                },
                "Period": period,
                "Stat": mq["stat"],
            },
            "ReturnData": True,
        })

    # Paginate through results
    results = {mq["id"]: [] for mq in metric_queries}
    kwargs = {
        "MetricDataQueries": queries,
        "StartTime": start_time,
        "EndTime": end_time,
        "ScanBy": "TimestampAscending",
    }

    while True:
        response = cw.get_metric_data(**kwargs)
        for result in response.get("MetricDataResults", []):
            metric_id = result["Id"]
            timestamps = result.get("Timestamps", [])
            values = result.get("Values", [])
            results[metric_id].extend(zip(timestamps, values))

        next_token = response.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token

    # Sort each metric by timestamp (should already be sorted, but be safe)
    for key in results:
        results[key].sort(key=lambda x: x[0])

    return results


def _append_invocation_metrics_body(info: list, metrics: dict) -> None:
    """Append the Invocations / Input / Output / Latency / Errors / Throttles
    sections to ``info``. Shared by the model-grain, system_profile and
    application_profile render paths of ``get_bedrock_model_invocation_metrics``.
    """
    # Invocations (RPM)
    inv = metrics.get("invocations") or []
    if inv:
        total_requests = sum(v for _, v in inv)
        avg_rpm = total_requests / len(inv)
        peak_rpm = max(v for _, v in inv)
        peak_ts = max(inv, key=lambda x: x[1])[0]
        info.append("\nInvocations (Requests):")
        info.append(f"  Total Requests: {total_requests:.0f}")
        info.append(f"  Average RPM: {avg_rpm:.2f}")
        info.append(f"  Peak RPM: {peak_rpm:.0f} (at {peak_ts.isoformat()}Z)")
    else:
        info.append("\nNo invocation data found for this time period")

    # Input Tokens
    inp = metrics.get("input_tokens") or []
    if inp:
        total_input = sum(v for _, v in inp)
        avg_input_tpm = total_input / len(inp)
        peak_input = max(v for _, v in inp)
        info.append("\nInput Tokens:")
        info.append(f"  Total: {total_input:.0f}")
        info.append(f"  Average TPM: {avg_input_tpm:.2f}")
        info.append(f"  Peak TPM: {peak_input:.0f}")

    # Output Tokens
    out = metrics.get("output_tokens") or []
    if out:
        total_output = sum(v for _, v in out)
        avg_output_tpm = total_output / len(out)
        peak_output = max(v for _, v in out)
        info.append("\nOutput Tokens:")
        info.append(f"  Total: {total_output:.0f}")
        info.append(f"  Average TPM: {avg_output_tpm:.2f}")
        info.append(f"  Peak TPM: {peak_output:.0f}")

    if not inp and not out:
        info.append("\nNo token metrics found for this time period")

    # Latency
    lat = metrics.get("latency") or []
    if lat:
        avg_latency = sum(v for _, v in lat) / len(lat)
        peak_latency = max(v for _, v in lat)
        peak_lat_ts = max(lat, key=lambda x: x[1])[0]
        info.append("\nInvocation Latency:")
        info.append(f"  Average: {avg_latency:.0f}ms")
        info.append(f"  Peak: {peak_latency:.0f}ms (at {peak_lat_ts.isoformat()}Z)")

    # Errors
    ce = metrics.get("client_errors") or []
    total_client_errors = sum(v for _, v in ce) if ce else 0
    se = metrics.get("server_errors") or []
    total_server_errors = sum(v for _, v in se) if se else 0
    info.append(f"\nClient Errors: {total_client_errors:.0f}")
    info.append(f"Server Errors: {total_server_errors:.0f}")

    # Throttles
    th = metrics.get("throttles") or []
    total_throttles = sum(v for _, v in th) if th else 0
    info.append(f"Throttled Requests: {total_throttles:.0f}")


def _render_model_grain_metrics(
    resolved_model_id: str,
    region: str,
    hours_back: int,
    start_time,
    end_time,
    start_time_iso: str | None,
    period: int,
    granularity: str,
) -> str:
    """Render model-grain invocation metrics (Task 8.1).

    Preserves the pre-existing ``get_bedrock_model_invocation_metrics``
    output shape byte-for-byte. The drill-down hint emitted here is the same
    text format as the pre-refactor tool (no ``model_id=...`` echo — that's only added
    on the profile-aware paths per Task 8.3).
    """
    # Region-aware CloudWatch model ID with the existing us.-prefix retry
    # dance for unknown profile types.
    cw_model_id, profile_type = _resolve_cw_model_id(resolved_model_id, region)

    model_info = get_model_info(resolved_model_id)
    display_name = model_info["name"] if model_info else resolved_model_id

    metrics = _get_metrics_batch(region, cw_model_id, start_time, end_time, period)

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

    info: list = []
    info.append(f"Bedrock Model Metrics for {display_name}")
    info.append(f"Model ID: {resolved_model_id}")
    if cw_model_id != resolved_model_id:
        info.append(
            f"CloudWatch Model ID: {cw_model_id} "
            f"({profile_type.lower().replace('_', '-')})"
        )
    info.append(f"Region: {region}")
    if start_time_iso:
        info.append(
            f"Time Range: {start_time.isoformat()}Z to {end_time.isoformat()}Z"
        )
    else:
        info.append(f"Time Range: Last {hours_back} hour(s)")
    info.append(f"Granularity: {granularity} averages ({period}s period)")
    info.append("=" * 80)

    _append_invocation_metrics_body(info, metrics)

    # Drill-down hint — unchanged from the pre-refactor tool so drilldown
    # eval scenarios keep passing. No ``model_id=...`` echo (Task 8.3 only
    # applies on the profile-aware paths).
    inv = metrics.get("invocations") or []
    if period > 60 and inv:
        peak_ts = max(inv, key=lambda x: x[1])[0]
        info.append(
            f"\nNote: Data shown at {granularity} granularity. "
            f"For minute-level detail around the peak, query with "
            f"start_time_iso around {peak_ts.isoformat()}Z"
        )

    return "\n".join(info)


def _render_system_profile_metrics(
    resolved: dict,
    original_ref: str,
    region: str,
    hours_back: int,
    start_time,
    end_time,
    start_time_iso: str | None,
    period: int,
    granularity: str,
) -> str:
    """Render invocation metrics for a System_Defined_Profile ref (Tasks 8.2, 8.3).

    Uses the resolved ``cw_model_id`` (e.g. ``us.anthropic.claude-...``)
    verbatim for the CloudWatch query and emits the ``Inference Profile:``
    label in the header alongside the underlying base ``Model ID:`` line.
    The drill-down hint at the end echoes the caller-supplied Profile_Ref.
    """
    cw_model_id = resolved.get("cw_model_id") or ""
    base_model_id = resolved.get("base_model_id") or cw_model_id
    model_info = get_model_info(base_model_id)
    display_name = model_info["name"] if model_info else base_model_id

    metrics = _get_metrics_batch(region, cw_model_id, start_time, end_time, period)

    info: list = []
    info.append(f"Bedrock Model Metrics for {display_name}")
    info.append(f"Model ID: {base_model_id}")
    info.append(f"Inference Profile: {cw_model_id}")
    info.append(f"Region: {region}")
    if start_time_iso:
        info.append(
            f"Time Range: {start_time.isoformat()}Z to {end_time.isoformat()}Z"
        )
    else:
        info.append(f"Time Range: Last {hours_back} hour(s)")
    info.append(f"Granularity: {granularity} averages ({period}s period)")
    info.append("=" * 80)

    _append_invocation_metrics_body(info, metrics)

    # Task 8.3 — drill-down hint echoes the original Profile_Ref so the
    # suggested follow-up targets the same Inference_Profile.
    inv = metrics.get("invocations") or []
    if period > 60 and inv:
        peak_ts = max(inv, key=lambda x: x[1])[0]
        info.append(
            f"\nNote: Data shown at {granularity} granularity. "
            f"For minute-level detail around the peak, query with "
            f"model_id='{original_ref}' and start_time_iso around "
            f"{peak_ts.isoformat()}Z"
        )

    return "\n".join(info)


def _render_app_profile_metrics(
    resolved: dict,
    original_ref: str,
    region: str,
    hours_back: int,
    start_time,
    end_time,
    start_time_iso: str | None,
    period: int,
    granularity: str,
) -> str:
    """Render invocation metrics for an Application_Profile ref (Tasks 8.2, 8.3).

    Uses the resolved ARN as the CloudWatch ``ModelId`` dimension value and
    emits the ``Application_Profile:`` / ``Application_Profile ARN:`` /
    ``Underlying Model ID:`` label trio in the header. The drill-down hint
    echoes the caller-supplied Profile_Ref (typically the friendly name).
    """
    arn = resolved.get("application_profile_arn") or resolved.get("cw_model_id") or ""
    cw_model_id = resolved.get("cw_model_id") or arn
    name = resolved.get("application_profile_name") or arn
    base_model_id = resolved.get("base_model_id")

    model_info = get_model_info(base_model_id) if base_model_id else None
    display_name = model_info["name"] if model_info else name

    metrics = _get_metrics_batch(region, cw_model_id, start_time, end_time, period)

    info: list = []
    info.append(f"Bedrock Model Metrics for {display_name}")
    info.append(f"Application_Profile: {name}")
    info.append(f"Application_Profile ARN: {arn}")
    if base_model_id:
        info.append(f"Underlying Model ID: {base_model_id}")
    info.append(f"Region: {region}")
    if start_time_iso:
        info.append(
            f"Time Range: {start_time.isoformat()}Z to {end_time.isoformat()}Z"
        )
    else:
        info.append(f"Time Range: Last {hours_back} hour(s)")
    info.append(f"Granularity: {granularity} averages ({period}s period)")
    info.append("=" * 80)

    _append_invocation_metrics_body(info, metrics)

    # Task 8.3 — drill-down hint echoes the caller-supplied Profile_Ref
    # (e.g. ``'marketing-bot'``) so the follow-up stays on the same
    # Application_Profile instead of collapsing to the base model.
    inv = metrics.get("invocations") or []
    if period > 60 and inv:
        peak_ts = max(inv, key=lambda x: x[1])[0]
        info.append(
            f"\nNote: Data shown at {granularity} granularity. "
            f"For minute-level detail around the peak, query with "
            f"model_id='{original_ref}' and start_time_iso around "
            f"{peak_ts.isoformat()}Z"
        )

    return "\n".join(info)


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------


@tool
def get_bedrock_model_invocation_metrics(
    model_id: str,
    region: str = DEFAULT_REGION,
    hours_back: int = 1,
    start_time_iso: str = None,
    end_time_iso: str = None,
) -> str:
    """Get CloudWatch metrics for Bedrock model invocations including TPM, RPM, latency, throttles, and errors.

    Uses GetMetricData to fetch all metrics in a single API call with proper pagination.
    Automatically adjusts granularity based on time range to handle large datasets correctly.

    ``model_id`` accepts a Profile_Ref (see ``check_quota_utilization`` for the
    accepted forms): a bare model ID or friendly alias keeps the existing
    model-grain behaviour, while System_Defined_Profile IDs, Application_Profile
    ARNs, and Application_Profile friendly names are routed through the
    profile-aware path and emit the Inference_Profile's label in the header.

    For drill-down analysis: use start_time_iso/end_time_iso to query a specific
    window at higher granularity after identifying peaks in a broader query.

    Args:
        model_id: Bedrock model ID, friendly name, or Profile_Ref.
        region: AWS region (default: agent's deployed region).
        hours_back: How many hours back to check metrics (default: 1). Ignored
            if start_time_iso is set.
        start_time_iso: Optional ISO-8601 start time for precise time windows
            (e.g., '2026-02-24T14:00:00Z').
        end_time_iso: Optional ISO-8601 end time (defaults to now if
            start_time_iso is set).

    Returns:
        String with invocation metrics including TPM, RPM, latency, throttle
        counts, and error counts.
    """
    try:
        # Task 8.1 — resolve the ref once, up front, so every branch below
        # has a clean ResolvedProfileRef to dispatch on.
        snapshot = get_snapshot_cached()
        resolved = resolve_profile_ref(model_id, snapshot)
        kind = resolved["kind"]

        # Task 8.4 — ambiguous ref: list candidates and stop without touching
        # CloudWatch (Req 4.5). Format mirrors check_quota_utilization exactly.
        if kind == "ambiguous":
            candidates = resolved.get("candidates") or []
            lines = [
                f"Multiple inference profiles match '{model_id}'. Please pick one:"
            ]
            for label in candidates:
                lines.append(f"  - {label}")
            return "\n".join(lines)

        # Task 8.4 — unresolved ref: point the user at get_customer_profile
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
            # Tasks 8.2, 8.3.
            return _render_app_profile_metrics(
                resolved, model_id, region, hours_back,
                start_time, end_time, start_time_iso, period, granularity,
            )

        if kind == "system_profile":
            # Tasks 8.2, 8.3.
            return _render_system_profile_metrics(
                resolved, model_id, region, hours_back,
                start_time, end_time, start_time_iso, period, granularity,
            )

        # kind == "model" — Task 8.1 back-compat path for drilldown scenarios.
        resolved_model_id = (
            resolved.get("base_model_id")
            or resolved.get("cw_model_id")
            or model_id
        )
        return _render_model_grain_metrics(
            resolved_model_id, region, hours_back,
            start_time, end_time, start_time_iso, period, granularity,
        )

    except Exception as e:
        logger.error(f"Error retrieving CloudWatch metrics: {e}", exc_info=True)
        return f"Error retrieving CloudWatch metrics: {str(e)}\n\nNote: Make sure you have proper AWS credentials and permissions for cloudwatch:GetMetricData"
