# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Response rendering helpers for profile-grain output."""


_PROFILE_SECTION_ORDER: dict[str, int] = {
    "on-demand": 0,
    "cross-region-geo (ap)": 1,
    "cross-region-geo (eu)": 2,
    "cross-region-geo (jp)": 3,
    "cross-region-geo (us)": 4,
    "cross-region-global": 5,
    "application-profile": 6,
}


def _find_model_entry(snapshot: dict | None, base_model_id: str) -> dict | None:
    """Return the ModelEntry in snapshot whose model_id matches, else None."""
    if not snapshot or not base_model_id:
        return None
    for model_entry in snapshot.get("models") or []:
        if model_entry.get("model_id") == base_model_id:
            return model_entry
    return None


def _access_pattern_invocations(pattern: dict) -> int:
    """Read invocations_24h from an AccessPattern's usage_summary block."""
    summary = pattern.get("usage_summary") or {}
    return int(summary.get("invocations_24h") or 0)


def build_multi_profile_disclosure_line(
    base_model_id: str, snapshot: dict | None
) -> str:
    """Build the Template A "usage split across N active profiles" disclosure line."""
    if snapshot is None:
        return ""

    model_entry = _find_model_entry(snapshot, base_model_id)
    if model_entry is None:
        return ""

    ap_labels: list[str] = []
    for pattern in model_entry.get("active_patterns") or []:
        if _access_pattern_invocations(pattern) > 0:
            cw_model_id = pattern.get("cw_model_id")
            if cw_model_id:
                ap_labels.append(cw_model_id)
    ap_labels.sort()

    app_labels: list[str] = []
    for app_profile in model_entry.get("app_profiles") or []:
        if int(app_profile.get("invocations_24h") or 0) > 0:
            name = app_profile.get("name")
            if name:
                app_labels.append(name)
    app_labels.sort()

    labels = ap_labels + app_labels
    if len(labels) < 2:
        return ""

    return (
        f"Note: usage for this model is split across {len(labels)} active "
        f"profiles ({', '.join(labels)}). "
        f"Ask for a breakdown to see per-profile metrics."
    )


def _row_sort_key(row: dict) -> tuple[int, str]:
    """Composer's sort key."""
    label = row["access_pattern_label"]
    primary = _PROFILE_SECTION_ORDER.get(label, 99)
    if label == "application-profile":
        name = row.get("application_profile_name") or ""
        return (primary, name.lower())
    return (primary, "")


def _row_display_name(row: dict) -> str:
    """Pick the header's display name for a row."""
    if row["access_pattern_label"] == "application-profile":
        name = row.get("application_profile_name")
        if name:
            return name
    return row.get("cw_model_id", "")


def _render_row(row: dict, lines: list[str]) -> None:
    """Append a single ProfileUsageRow to lines as a formatted section."""
    label = row["access_pattern_label"]
    display = _row_display_name(row)
    header = f"[ {label} ]  {display}"
    if row.get("no_recent_usage_label"):
        header += " (no recent usage)"
    if row.get("inactive_available_label"):
        header += " (inactive — available but unused)"
    lines.append(header)

    if label == "application-profile":
        arn = row.get("application_profile_arn")
        if arn:
            lines.append(f"  ARN: {arn}")

    lines.append(f"  Invocations: {int(row['invocations_in_window']):,}")
    lines.append(f"  Peak RPM: {int(row['peak_rpm']):,}")
    lines.append(f"  Peak Input TPM: {int(row['peak_input_tpm']):,}")
    lines.append(f"  Peak Output TPM: {int(row['peak_output_tpm']):,}")


def compose_profile_grain_output(
    display_name: str,
    region: str,
    hours_back: int,
    rows: list[dict],
) -> str:
    """Render Templates B (multi-section) and C (single-section) profile-grain output."""
    lines: list[str] = []
    lines.append(
        f"{display_name} — per-profile breakdown "
        f"({region}, past {hours_back}h)"
    )
    lines.append("=" * 80)
    lines.append("")

    if not rows:
        lines.append("(no data)")
        return "\n".join(lines)

    sorted_rows = sorted(rows, key=_row_sort_key)
    for idx, row in enumerate(sorted_rows):
        _render_row(row, lines)
        if idx != len(sorted_rows) - 1:
            lines.append("")

    if len(sorted_rows) >= 2:
        total_invocations = sum(
            int(row["invocations_in_window"]) for row in sorted_rows
        )
        peak_rpm = max(row["peak_rpm"] for row in sorted_rows)
        peak_input_tpm = max(row["peak_input_tpm"] for row in sorted_rows)
        peak_output_tpm = max(row["peak_output_tpm"] for row in sorted_rows)
        lines.append("")
        lines.append("-" * 80)
        lines.append("Totals (across profiles above):")
        lines.append(f"  Invocations: {total_invocations:,}")
        lines.append(f"  Peak RPM: {int(peak_rpm):,} (max)")
        lines.append(f"  Peak Input TPM: {int(peak_input_tpm):,} (max)")
        lines.append(f"  Peak Output TPM: {int(peak_output_tpm):,} (max)")

    return "\n".join(lines)


def compose_no_usage_found_message(
    display_name: str,
    region: str,
    hours_back: int,
    labels: list[str],
) -> str:
    """Render the "no usage found in window" message."""
    lines = [
        f"No usage found for {display_name} in {region} (past {hours_back}h)."
    ]
    if labels:
        lines.append("Profiles checked:")
        for label in labels:
            lines.append(f"  - {label}")
    return "\n".join(lines)


def compose_shared_quota_warning(
    resolved: dict, snapshot: dict | None
) -> str:
    """Render the Template D shared-quota warning block."""
    if resolved.get("kind") != "application_profile":
        return ""
    if snapshot is None:
        return ""
    scope = resolved.get("underlying_quota_scope")
    if scope is None:
        return ""
    base_model_id = resolved.get("base_model_id")
    if base_model_id is None:
        return ""

    model_entry = _find_model_entry(snapshot, base_model_id)
    if model_entry is None:
        return ""

    if scope == "cross-region":
        allowed_pattern_types = ("cross-region-geo", "cross-region-global")
        direct_traffic_label = "direct cross-region traffic"
        allowed_wraps = "cross-region"
    elif scope == "on-demand":
        allowed_pattern_types = ("on-demand",)
        direct_traffic_label = "direct on-demand traffic"
        allowed_wraps = "on-demand"
    else:
        return ""

    cotenants: list[str] = []

    for pattern in model_entry.get("active_patterns") or []:
        if pattern.get("pattern_type") not in allowed_pattern_types:
            continue
        if _access_pattern_invocations(pattern) <= 0:
            continue
        cw_model_id = pattern.get("cw_model_id")
        if cw_model_id:
            cotenants.append(f"{cw_model_id} ({direct_traffic_label})")

    resolved_arn = resolved.get("application_profile_arn")
    for app_profile in model_entry.get("app_profiles") or []:
        if app_profile.get("wraps") != allowed_wraps:
            continue
        if int(app_profile.get("invocations_24h") or 0) <= 0:
            continue
        if app_profile.get("arn") == resolved_arn:
            continue
        name = app_profile.get("name")
        if name:
            cotenants.append(f"{name} (application-inference-profile)")

    if not cotenants:
        return ""

    lines = ["⚠  Other traffic also counts against this limit:"]
    for label in cotenants:
        lines.append(f"   - {label}")
    lines.append(
        "   Check check_quota_utilization with the bare model ID to see "
        "combined usage."
    )
    return "\n".join(lines)
