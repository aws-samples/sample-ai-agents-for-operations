"""
Unit tests for the four response composer helpers in ``src/agent.py``.

Covers:

- ``build_multi_profile_disclosure_line`` — Template A (Req 1.2, 1.4)
- ``compose_profile_grain_output``        — Templates B and C (Req 2.1–2.4,
                                             3.3)
- ``compose_no_usage_found_message``      — Error Handling row 2 (Req 2.5)
- ``compose_shared_quota_warning``        — Template D (Req 5.3, 5.4)

The test shape follows the conventions established in
``test_resolve_profile_ref.py`` and ``test_is_active_inference_profile.py``:

- Third-party imports are stubbed via ``sys.modules.setdefault`` so
  ``agent.py`` imports without ever touching boto3 / strands / bedrock_agentcore.
- The ``agent`` module is loaded exactly once via ``_load_agent_module``, which
  mocks ``boto3.client`` at import time.
- ``pytest.mark.parametrize`` drives table-driven cases.
- Snapshot fixtures follow the schema documented in
  ``.kiro/specs/customer-profile/design.md`` §"Data Models".

No DynamoDB is needed: all four composers are pure functions over dicts and
lists.
"""


from helpers.response_composer import (
    build_multi_profile_disclosure_line,
    compose_profile_grain_output,
    compose_no_usage_found_message,
    compose_shared_quota_warning,
)


# ---------------------------------------------------------------------------
# Shared fixture factories
# ---------------------------------------------------------------------------

BASE_MODEL = "anthropic.claude-sonnet-4-6-v1:0"
MKT_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/mkt-bot-abc123"
)
ANALYTICS_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/analytics-bot-def456"
)
RESEARCH_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/research-bot-ghi789"
)


def _make_access_pattern(
    pattern_type: str,
    cw_model_id: str,
    invocations_24h: int = 100,
    geography: str | None = None,
) -> dict:
    """Build a minimal AccessPattern entry for Snapshot fixtures."""
    return {
        "pattern_type": pattern_type,
        "cw_model_id": cw_model_id,
        "regions": ["us-east-1"],
        "geography": geography,
        "quota_limits": {"rpm_limit": 100, "tpm_limit": 100_000},
        "usage_summary": {
            "invocations_24h": invocations_24h,
            "peak_rpm": 10,
            "peak_input_tpm": 1_000,
            "peak_output_tpm": 200,
        },
    }


def _make_app_profile(
    name: str,
    arn: str,
    invocations_24h: int = 0,
    wraps: str | None = "cross-region",
) -> dict:
    """Build a minimal AppProfile entry for Snapshot fixtures."""
    entry = {
        "name": name,
        "arn": arn,
        "tags": {},
        "invocations_24h": invocations_24h,
    }
    if wraps is not None:
        entry["wraps"] = wraps
    return entry


def _snapshot_with(
    access_patterns: list[dict] | None = None,
    app_profiles: list[dict] | None = None,
    model_id: str = BASE_MODEL,
) -> dict:
    """Build a Snapshot with a single ModelEntry pre-populated."""
    return {
        "PK": "customer-profile",
        "SK": "latest",
        "models": [
            {
                "model_id": model_id,
                "provider": "Anthropic",
                "display_name": "Claude Sonnet 4.6",
                "active_patterns": access_patterns or [],
                "app_profiles": app_profiles or [],
            }
        ],
    }


def _make_row(
    access_pattern_label: str,
    *,
    cw_model_id: str | None = None,
    application_profile_name: str | None = None,
    application_profile_arn: str | None = None,
    invocations_in_window: int = 100,
    peak_rpm: float = 10,
    peak_input_tpm: float = 1_000,
    peak_output_tpm: float = 200,
    no_recent_usage_label: bool = False,
    inactive_available_label: bool = False,
) -> dict:
    """Build a ProfileUsageRow with sensible defaults."""
    return {
        "cw_model_id": cw_model_id
        or (application_profile_arn if application_profile_arn else BASE_MODEL),
        "access_pattern_label": access_pattern_label,
        "application_profile_name": application_profile_name,
        "application_profile_arn": application_profile_arn,
        "invocations_in_window": invocations_in_window,
        "peak_rpm": peak_rpm,
        "peak_input_tpm": peak_input_tpm,
        "peak_output_tpm": peak_output_tpm,
        "no_recent_usage_label": no_recent_usage_label,
        "inactive_available_label": inactive_available_label,
    }


def _make_resolved(
    *,
    kind: str = "application_profile",
    cw_model_id: str | None = MKT_BOT_ARN,
    base_model_id: str | None = BASE_MODEL,
    application_profile_name: str | None = "marketing-bot",
    application_profile_arn: str | None = MKT_BOT_ARN,
    underlying_quota_scope: str | None = "cross-region",
) -> dict:
    """Build a ResolvedProfileRef dict with sensible defaults."""
    return {
        "kind": kind,
        "cw_model_id": cw_model_id,
        "base_model_id": base_model_id,
        "application_profile_name": application_profile_name,
        "application_profile_arn": application_profile_arn,
        "underlying_quota_scope": underlying_quota_scope,
        "candidates": [],
        "unresolved_ref": None,
    }


# ===========================================================================
# build_multi_profile_disclosure_line  (Template A — Req 1.2, 1.4)
# ===========================================================================


class TestBuildMultiProfileDisclosureLine:
    """Template A disclosure-line builder.

    Covers the "2+ Active_Inference_Profiles with invocations > 0" trigger
    from Req 1.2 and the "Snapshot=None ⇒ suppress" fallback from Req 1.4.
    """

    # --- Suppression cases (return "") ----------------------------------

    def test_snapshot_none_returns_empty(self):
        """Req 1.4 — a ``None`` Snapshot suppresses the disclosure line silently."""
        result = build_multi_profile_disclosure_line(BASE_MODEL, None)
        # The result is an empty string, not just falsy.
        assert isinstance(result, str)
        assert result == ""

    def test_model_not_in_snapshot_returns_empty(self):
        """No matching ModelEntry → nothing to disclose."""
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern("on-demand", BASE_MODEL, invocations_24h=100)
            ],
            model_id="some.other.model-v1:0",
        )
        assert (
            build_multi_profile_disclosure_line(BASE_MODEL, snapshot)
            == ""
        )

    def test_zero_qualifying_profiles_returns_empty(self):
        """ModelEntry with no invocations > 0 entries → ``""``."""
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern("on-demand", BASE_MODEL, invocations_24h=0)
            ],
            app_profiles=[
                _make_app_profile("quiet-bot", MKT_BOT_ARN, invocations_24h=0)
            ],
        )
        assert (
            build_multi_profile_disclosure_line(BASE_MODEL, snapshot)
            == ""
        )

    def test_single_qualifying_profile_returns_empty(self):
        """Exactly 1 qualifying profile → no split to disclose → ``""``."""
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "on-demand", BASE_MODEL, invocations_24h=100
                )
            ]
        )
        assert (
            build_multi_profile_disclosure_line(BASE_MODEL, snapshot)
            == ""
        )

    def test_zero_invocation_app_profile_not_counted(self):
        """An AppProfile with ``invocations_24h == 0`` does NOT count toward 2+.

        One qualifying AccessPattern + one zero-invocation AppProfile → 1
        qualifying profile → empty output.
        """
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "on-demand", BASE_MODEL, invocations_24h=100
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "quiet-bot", MKT_BOT_ARN, invocations_24h=0
                )
            ],
        )
        assert (
            build_multi_profile_disclosure_line(BASE_MODEL, snapshot)
            == ""
        )

    def test_zero_invocation_access_pattern_not_counted(self):
        """An AccessPattern with ``invocations_24h == 0`` does NOT count toward 2+.

        One zero AccessPattern + one qualifying AppProfile → 1 qualifying
        profile → empty output.
        """
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "on-demand", BASE_MODEL, invocations_24h=0
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot", MKT_BOT_ARN, invocations_24h=490
                )
            ],
        )
        assert (
            build_multi_profile_disclosure_line(BASE_MODEL, snapshot)
            == ""
        )

    # --- Happy paths (return a non-empty disclosure line) ---------------

    def test_two_qualifying_profiles_one_ap_one_app(self):
        """1 AccessPattern + 1 AppProfile, both invocations > 0 → disclosure emitted.

        Verifies the "2 active profiles" headline, both labels appear, and
        AccessPattern label precedes the AppProfile label (Snapshot order).
        """
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=11_437,
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot", MKT_BOT_ARN, invocations_24h=490
                )
            ],
        )

        out = build_multi_profile_disclosure_line(BASE_MODEL, snapshot)

        assert out.startswith("Note: usage for this model is split across 2 ")
        assert "active profiles" in out
        assert "us.anthropic.claude-sonnet-4-6-v1:0" in out
        assert "marketing-bot" in out
        assert out.endswith(
            "Ask for a breakdown to see per-profile metrics."
        )
        # Label ordering: AccessPattern before AppProfile.
        assert out.index("us.anthropic.claude-sonnet-4-6-v1:0") < out.index(
            "marketing-bot"
        )

    def test_three_qualifying_profiles(self):
        """3 qualifying profiles → "3 active profiles" with all 3 labels."""
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "on-demand", BASE_MODEL, invocations_24h=920
                ),
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=11_437,
                ),
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot", MKT_BOT_ARN, invocations_24h=490
                )
            ],
        )

        out = build_multi_profile_disclosure_line(BASE_MODEL, snapshot)

        assert "split across 3 active profiles" in out
        assert BASE_MODEL in out
        assert "us.anthropic.claude-sonnet-4-6-v1:0" in out
        assert "marketing-bot" in out

    def test_label_ordering_access_patterns_first(self):
        """AccessPatterns always precede AppProfiles in the emitted label list."""
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "on-demand", BASE_MODEL, invocations_24h=100
                ),
                _make_access_pattern(
                    "cross-region-global",
                    "global.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=100,
                ),
            ],
            app_profiles=[
                _make_app_profile(
                    "aaa-bot", MKT_BOT_ARN, invocations_24h=100
                ),
                _make_app_profile(
                    "zzz-bot", ANALYTICS_BOT_ARN, invocations_24h=100
                ),
            ],
        )

        out = build_multi_profile_disclosure_line(BASE_MODEL, snapshot)

        # Both AccessPattern labels must precede both AppProfile labels.
        idx_base = out.index(BASE_MODEL)
        idx_global = out.index("global.anthropic.claude-sonnet-4-6-v1:0")
        idx_aaa = out.index("aaa-bot")
        idx_zzz = out.index("zzz-bot")
        assert idx_base < idx_aaa
        assert idx_base < idx_zzz
        assert idx_global < idx_aaa
        assert idx_global < idx_zzz


# ===========================================================================
# compose_profile_grain_output  (Templates B and C — Req 2.1–2.4, 3.3)
# ===========================================================================


class TestComposeProfileGrainOutput:
    """Profile-grain composer for Templates B (multi-section) and C (single).

    Covers Req 2.1 (fixed section ordering), Req 2.2 (per-row fields),
    Req 2.3 (Totals row for ≥2 sections), Req 2.4 (no Totals for a single
    section), and Req 3.3 (``(no recent usage)`` inline label).
    """

    # --- Empty / degenerate input --------------------------------------

    def test_empty_rows_returns_header_plus_no_data(self):
        """Defensive empty-input path — composer emits the header + ``(no data)``."""
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, []
        )
        assert "Claude Sonnet 4.6" in out
        assert "us-east-1" in out
        assert "past 24h" in out
        assert "(no data)" in out
        # No Totals block for empty input.
        assert "Totals" not in out

    # --- Single section (Req 2.4 — Template C) -------------------------

    def test_single_row_on_demand_no_totals_no_divider(self):
        """Single on-demand row → Template C: no Totals, no divider line."""
        row = _make_row(
            "on-demand",
            cw_model_id=BASE_MODEL,
            invocations_in_window=920,
            peak_rpm=18,
            peak_input_tpm=4_200,
            peak_output_tpm=610,
        )
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, [row]
        )

        assert "[ on-demand ]" in out
        assert BASE_MODEL in out
        # Single-row metric values are present.
        assert "Invocations: 920" in out
        assert "Peak RPM: 18" in out
        assert "Peak Input TPM: 4,200" in out
        assert "Peak Output TPM: 610" in out
        # Req 2.4 — no Totals row, no divider of 80 dashes.
        assert "Totals" not in out
        assert "-" * 80 not in out

    def test_single_row_app_profile_includes_arn(self):
        """Single application-profile row → ARN appears in the body."""
        row = _make_row(
            "application-profile",
            application_profile_name="marketing-bot",
            application_profile_arn=MKT_BOT_ARN,
            cw_model_id=MKT_BOT_ARN,
            invocations_in_window=490,
            peak_rpm=9,
            peak_input_tpm=2_800,
            peak_output_tpm=420,
        )
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, [row]
        )

        assert "[ application-profile ]" in out
        # Section header prefers the friendly name for app-profiles.
        assert "marketing-bot" in out
        # ARN line is emitted.
        assert f"ARN: {MKT_BOT_ARN}" in out
        assert "Invocations: 490" in out
        # Single section → no Totals.
        assert "Totals" not in out

    # --- Multi-section (Req 2.1, 2.3 — Template B) ---------------------

    def test_two_rows_emit_totals_with_sum_and_max(self):
        """Req 2.3 — Totals row sums invocations and takes max of peaks."""
        rows = [
            _make_row(
                "on-demand",
                cw_model_id=BASE_MODEL,
                invocations_in_window=920,
                peak_rpm=18,
                peak_input_tpm=4_200,
                peak_output_tpm=610,
            ),
            _make_row(
                "cross-region-geo (us)",
                cw_model_id="us.anthropic.claude-sonnet-4-6-v1:0",
                invocations_in_window=11_437,
                peak_rpm=142,
                peak_input_tpm=58_200,
                peak_output_tpm=4_100,
            ),
        ]
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, rows
        )

        # Both sections emitted.
        assert "[ on-demand ]" in out
        assert "[ cross-region-geo (us) ]" in out
        # Totals block is present.
        assert "Totals (across profiles above)" in out
        # Sum of invocations: 920 + 11437 = 12357 — use thousands-comma form.
        assert "Invocations: 12,357" in out
        # Peaks are max, not sum.
        assert "Peak RPM: 142 (max)" in out
        assert "Peak Input TPM: 58,200 (max)" in out
        assert "Peak Output TPM: 4,100 (max)" in out
        # Divider line above the Totals block.
        assert "-" * 80 in out

    def test_fixed_ordering_rows_out_of_order(self):
        """Req 2.1 — composer sorts regardless of input order.

        Feed the rows in reverse order and assert the emitted section order
        is still on-demand → cross-region-geo (us) → cross-region-global →
        application-profile.
        """
        rows = [
            _make_row(
                "application-profile",
                application_profile_name="marketing-bot",
                application_profile_arn=MKT_BOT_ARN,
                cw_model_id=MKT_BOT_ARN,
            ),
            _make_row(
                "cross-region-global",
                cw_model_id="global.anthropic.claude-sonnet-4-6-v1:0",
            ),
            _make_row(
                "cross-region-geo (us)",
                cw_model_id="us.anthropic.claude-sonnet-4-6-v1:0",
            ),
            _make_row("on-demand", cw_model_id=BASE_MODEL),
        ]
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, rows
        )

        idx_on_demand = out.index("[ on-demand ]")
        idx_geo_us = out.index("[ cross-region-geo (us) ]")
        idx_global = out.index("[ cross-region-global ]")
        idx_app = out.index("[ application-profile ]")

        assert idx_on_demand < idx_geo_us < idx_global < idx_app

    def test_geo_order_is_ap_eu_jp_us(self):
        """Req 2.1 — cross-region-geo sections order alphabetically: ap, eu, jp, us."""
        rows = [
            _make_row(
                "cross-region-geo (us)",
                cw_model_id="us.anthropic.claude-sonnet-4-6-v1:0",
            ),
            _make_row(
                "cross-region-geo (jp)",
                cw_model_id="jp.anthropic.claude-sonnet-4-6-v1:0",
            ),
            _make_row(
                "cross-region-geo (eu)",
                cw_model_id="eu.anthropic.claude-sonnet-4-6-v1:0",
            ),
            _make_row(
                "cross-region-geo (ap)",
                cw_model_id="ap.anthropic.claude-sonnet-4-6-v1:0",
            ),
        ]
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, rows
        )

        idx_ap = out.index("[ cross-region-geo (ap) ]")
        idx_eu = out.index("[ cross-region-geo (eu) ]")
        idx_jp = out.index("[ cross-region-geo (jp) ]")
        idx_us = out.index("[ cross-region-geo (us) ]")

        assert idx_ap < idx_eu < idx_jp < idx_us

    def test_app_profiles_sorted_alphabetically_by_name(self):
        """Req 2.1 — ``zzz-bot`` after ``aaa-bot`` regardless of input order."""
        rows = [
            _make_row(
                "application-profile",
                application_profile_name="zzz-bot",
                application_profile_arn=ANALYTICS_BOT_ARN,
                cw_model_id=ANALYTICS_BOT_ARN,
            ),
            _make_row(
                "application-profile",
                application_profile_name="aaa-bot",
                application_profile_arn=MKT_BOT_ARN,
                cw_model_id=MKT_BOT_ARN,
            ),
        ]
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, rows
        )

        assert out.index("aaa-bot") < out.index("zzz-bot")

    # --- Labels (Req 3.3 and 3.5) --------------------------------------

    def test_no_recent_usage_label_rendered_for_app_profile(self):
        """Req 3.3 — ``no_recent_usage_label=True`` on an app-profile row → inline label."""
        row = _make_row(
            "application-profile",
            application_profile_name="quiet-bot",
            application_profile_arn=MKT_BOT_ARN,
            cw_model_id=MKT_BOT_ARN,
            invocations_in_window=0,
            peak_rpm=0,
            peak_input_tpm=0,
            peak_output_tpm=0,
            no_recent_usage_label=True,
        )
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, [row]
        )

        assert "(no recent usage)" in out
        # The label should appear on the section header line, not buried
        # down in the body.
        header_line = next(
            line for line in out.splitlines() if "[ application-profile ]" in line
        )
        assert "(no recent usage)" in header_line

    def test_inactive_available_label_rendered_for_system_profile(self):
        """Req 3.5 — ``inactive_available_label=True`` on a system-profile row → inline label."""
        row = _make_row(
            "cross-region-global",
            cw_model_id="global.anthropic.claude-sonnet-4-6-v1:0",
            invocations_in_window=0,
            peak_rpm=0,
            peak_input_tpm=0,
            peak_output_tpm=0,
            inactive_available_label=True,
        )
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, [row]
        )

        assert "(inactive — available but unused)" in out
        header_line = next(
            line for line in out.splitlines() if "[ cross-region-global ]" in line
        )
        assert "(inactive — available but unused)" in header_line

    # --- Per-row field presence (Req 2.2) ------------------------------

    def test_per_row_fields_complete_for_access_pattern(self):
        """Req 2.2 — AccessPattern rows carry CW_Model_ID + metric fields."""
        row = _make_row(
            "cross-region-geo (us)",
            cw_model_id="us.anthropic.claude-sonnet-4-6-v1:0",
            invocations_in_window=11_437,
            peak_rpm=142,
            peak_input_tpm=58_200,
            peak_output_tpm=4_100,
        )
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, [row]
        )

        # CW_Model_ID is in the section header.
        assert "us.anthropic.claude-sonnet-4-6-v1:0" in out
        # Access pattern label.
        assert "[ cross-region-geo (us) ]" in out
        # All four metric fields.
        assert "Invocations: 11,437" in out
        assert "Peak RPM: 142" in out
        assert "Peak Input TPM: 58,200" in out
        assert "Peak Output TPM: 4,100" in out
        # No ARN line for non-app-profile rows.
        assert "ARN:" not in out

    def test_per_row_fields_complete_for_app_profile(self):
        """Req 2.2 — application-profile rows also emit the ARN line."""
        row = _make_row(
            "application-profile",
            application_profile_name="marketing-bot",
            application_profile_arn=MKT_BOT_ARN,
            cw_model_id=MKT_BOT_ARN,
            invocations_in_window=490,
            peak_rpm=9,
            peak_input_tpm=2_800,
            peak_output_tpm=420,
        )
        out = compose_profile_grain_output(
            "Claude Sonnet 4.6", "us-east-1", 24, [row]
        )

        assert "[ application-profile ]" in out
        assert "marketing-bot" in out
        assert f"ARN: {MKT_BOT_ARN}" in out
        assert "Invocations: 490" in out
        assert "Peak RPM: 9" in out
        assert "Peak Input TPM: 2,800" in out
        assert "Peak Output TPM: 420" in out


# ===========================================================================
# compose_no_usage_found_message  (Error Handling row 2 — Req 2.5)
# ===========================================================================


class TestComposeNoUsageFoundMessage:
    """The empty-window fallback message for profile-aware tools."""

    def test_empty_labels_headline_only(self):
        """Empty label list → only the headline; no "Profiles checked:" block."""
        out = compose_no_usage_found_message(
            "Claude Sonnet 4.6", "us-east-1", 24, []
        )

        assert "Claude Sonnet 4.6" in out
        assert "us-east-1" in out
        assert "24h" in out
        assert "No usage found" in out
        assert "Profiles checked:" not in out

    def test_single_label_renders_bullet_list(self):
        """Single label still renders the bullet-list form for output consistency."""
        out = compose_no_usage_found_message(
            "Claude Sonnet 4.6",
            "us-east-1",
            24,
            ["us.anthropic.claude-sonnet-4-6-v1:0"],
        )

        assert "Profiles checked:" in out
        assert "  - us.anthropic.claude-sonnet-4-6-v1:0" in out

    def test_multiple_labels_each_on_own_line(self):
        """Each label appears on its own bullet line, in input order."""
        labels = [
            "anthropic.claude-sonnet-4-6-v1:0",
            "us.anthropic.claude-sonnet-4-6-v1:0",
            "marketing-bot",
        ]
        out = compose_no_usage_found_message(
            "Claude Sonnet 4.6", "us-east-1", 24, labels
        )

        for label in labels:
            assert f"  - {label}" in out
        # Preserve input order.
        idx0 = out.index(labels[0])
        idx1 = out.index(labels[1])
        idx2 = out.index(labels[2])
        assert idx0 < idx1 < idx2

    def test_headline_includes_model_region_hours(self):
        """Headline format names the model, region, and lookback hours."""
        out = compose_no_usage_found_message(
            "Nova Pro", "eu-west-1", 12, ["eu.amazon.nova-pro-v1:0"]
        )

        # First line carries the headline.
        first_line = out.splitlines()[0]
        assert "Nova Pro" in first_line
        assert "eu-west-1" in first_line
        assert "12h" in first_line


# ===========================================================================
# compose_shared_quota_warning  (Template D — Req 5.3, 5.4)
# ===========================================================================


class TestComposeSharedQuotaWarning:
    """Shared-quota co-tenant warning block.

    Covers the short-circuit guards + every co-tenant selection rule documented
    in the composer docstring. The warning block itself is inspected line by
    line so regressions in formatting are caught (Req 5.3, 5.4).
    """

    # --- Short-circuit guards -------------------------------------------

    def test_non_application_profile_kind_returns_empty(self):
        """``kind != "application_profile"`` short-circuits to ``""``."""
        resolved = _make_resolved(
            kind="system_profile",
            cw_model_id="us.anthropic.claude-sonnet-4-6-v1:0",
            application_profile_arn=None,
            application_profile_name=None,
        )
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=1_000,
                )
            ]
        )
        assert compose_shared_quota_warning(resolved, snapshot) == ""

    def test_snapshot_none_returns_empty(self):
        """A ``None`` Snapshot yields an empty warning."""
        resolved = _make_resolved()
        assert compose_shared_quota_warning(resolved, None) == ""

    def test_underlying_quota_scope_none_returns_empty(self):
        """Req 5.5 — scope=None means we cannot determine co-tenants."""
        resolved = _make_resolved(underlying_quota_scope=None)
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=1_000,
                )
            ]
        )
        assert compose_shared_quota_warning(resolved, snapshot) == ""

    def test_base_model_id_none_returns_empty(self):
        """Cannot locate the ModelEntry without a base model ID."""
        resolved = _make_resolved(base_model_id=None)
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=1_000,
                )
            ]
        )
        assert compose_shared_quota_warning(resolved, snapshot) == ""

    def test_model_not_in_snapshot_returns_empty(self):
        """ModelEntry absent from Snapshot → nothing to warn about."""
        resolved = _make_resolved()
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=1_000,
                )
            ],
            model_id="some.unrelated.model-v1:0",
        )
        assert compose_shared_quota_warning(resolved, snapshot) == ""

    def test_no_co_tenants_returns_empty(self):
        """The resolved profile is alone on the Underlying_Quota → no warning."""
        resolved = _make_resolved()
        snapshot = _snapshot_with(
            app_profiles=[
                # Only the resolved profile itself, no co-tenants.
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                )
            ]
        )
        assert compose_shared_quota_warning(resolved, snapshot) == ""

    # --- Cross-region scope: happy paths --------------------------------

    def test_cross_region_lists_cross_region_geo_access_pattern(self):
        """AccessPattern ``pattern_type="cross-region-geo"`` → direct cross-region traffic label."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=11_437,
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                )
            ],
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        assert "⚠  Other traffic also counts against this limit:" in out
        assert (
            "us.anthropic.claude-sonnet-4-6-v1:0 (direct cross-region traffic)"
            in out
        )
        assert (
            "Check check_quota_utilization with the bare model ID to see "
            "combined usage." in out
        )

    def test_cross_region_lists_cross_region_global_access_pattern(self):
        """AccessPattern ``pattern_type="cross-region-global"`` → direct cross-region traffic label."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-global",
                    "global.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=5_000,
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                )
            ],
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        assert (
            "global.anthropic.claude-sonnet-4-6-v1:0 (direct cross-region traffic)"
            in out
        )

    def test_cross_region_lists_other_app_profile_wrapping_cross_region(self):
        """Other AppProfile with ``wraps="cross-region"`` → application-inference-profile label."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                ),
                _make_app_profile(
                    "analytics-bot",
                    ANALYTICS_BOT_ARN,
                    invocations_24h=120,
                    wraps="cross-region",
                ),
            ]
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        assert "analytics-bot (application-inference-profile)" in out
        # Resolved profile itself is NOT listed.
        assert "marketing-bot (application-inference-profile)" not in out

    def test_cross_region_excludes_on_demand_access_pattern(self):
        """On-demand direct traffic is on a different quota → not a co-tenant."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "on-demand", BASE_MODEL, invocations_24h=920
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                )
            ],
        )
        # Only the resolved profile's scope is cross-region; the AccessPattern
        # is on-demand and the only other AppProfile wraps on-demand → no
        # co-tenants at the cross-region scope.
        out = compose_shared_quota_warning(resolved, snapshot)
        assert out == ""

    def test_cross_region_excludes_other_app_profile_wrapping_on_demand(self):
        """Other AppProfile with ``wraps="on-demand"`` → not a cross-region co-tenant."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                ),
                _make_app_profile(
                    "analytics-bot",
                    ANALYTICS_BOT_ARN,
                    invocations_24h=120,
                    wraps="on-demand",
                ),
            ]
        )
        out = compose_shared_quota_warning(resolved, snapshot)
        assert out == ""

    # --- On-demand scope: happy paths -----------------------------------

    def test_on_demand_lists_on_demand_access_pattern(self):
        """AccessPattern ``pattern_type="on-demand"`` → direct on-demand traffic label."""
        resolved = _make_resolved(
            underlying_quota_scope="on-demand",
            cw_model_id=ANALYTICS_BOT_ARN,
            application_profile_name="analytics-bot",
            application_profile_arn=ANALYTICS_BOT_ARN,
        )
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "on-demand", BASE_MODEL, invocations_24h=920
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "analytics-bot",
                    ANALYTICS_BOT_ARN,
                    invocations_24h=120,
                    wraps="on-demand",
                )
            ],
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        assert f"{BASE_MODEL} (direct on-demand traffic)" in out

    def test_on_demand_lists_other_app_profile_wrapping_on_demand(self):
        """Other AppProfile with ``wraps="on-demand"`` → application-inference-profile label."""
        resolved = _make_resolved(
            underlying_quota_scope="on-demand",
            cw_model_id=ANALYTICS_BOT_ARN,
            application_profile_name="analytics-bot",
            application_profile_arn=ANALYTICS_BOT_ARN,
        )
        snapshot = _snapshot_with(
            app_profiles=[
                _make_app_profile(
                    "analytics-bot",
                    ANALYTICS_BOT_ARN,
                    invocations_24h=120,
                    wraps="on-demand",
                ),
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="on-demand",
                ),
            ]
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        assert "marketing-bot (application-inference-profile)" in out
        # Resolved profile itself is NOT listed.
        assert "analytics-bot (application-inference-profile)" not in out

    # --- Filtering: zero-invocation and self-exclusion ------------------

    def test_resolved_profile_itself_excluded_by_arn(self):
        """The resolved AppProfile's own entry is never listed as a co-tenant."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                ),
                _make_app_profile(
                    "analytics-bot",
                    ANALYTICS_BOT_ARN,
                    invocations_24h=120,
                    wraps="cross-region",
                ),
            ]
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        # The caller's own profile is filtered out.
        # (note: name might appear in header/footer, so we match on the
        # labelled co-tenant form.)
        assert "marketing-bot (application-inference-profile)" not in out
        assert "analytics-bot (application-inference-profile)" in out

    def test_zero_invocation_access_pattern_excluded(self):
        """AccessPattern with ``invocations_24h == 0`` is not listed."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=0,
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                ),
                _make_app_profile(
                    "analytics-bot",
                    ANALYTICS_BOT_ARN,
                    invocations_24h=120,
                    wraps="cross-region",
                ),
            ],
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        # The zero-invocation AccessPattern is filtered out; only the other
        # AppProfile is listed.
        assert "us.anthropic.claude-sonnet-4-6-v1:0" not in out
        assert "analytics-bot (application-inference-profile)" in out

    def test_zero_invocation_app_profile_excluded(self):
        """AppProfile with ``invocations_24h == 0`` is not listed as co-tenant."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=11_437,
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                ),
                _make_app_profile(
                    "research-bot",
                    RESEARCH_BOT_ARN,
                    invocations_24h=0,
                    wraps="cross-region",
                ),
            ],
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        assert "research-bot" not in out
        # Direct traffic is still listed.
        assert (
            "us.anthropic.claude-sonnet-4-6-v1:0 (direct cross-region traffic)"
            in out
        )

    # --- Output structure (Req 5.3, 5.4 — full block shape) -------------

    def test_output_contains_header_and_footer_lines(self):
        """The full warning block renders the header, bullets, and footer."""
        resolved = _make_resolved(underlying_quota_scope="cross-region")
        snapshot = _snapshot_with(
            access_patterns=[
                _make_access_pattern(
                    "cross-region-geo",
                    "us.anthropic.claude-sonnet-4-6-v1:0",
                    invocations_24h=11_437,
                )
            ],
            app_profiles=[
                _make_app_profile(
                    "marketing-bot",
                    MKT_BOT_ARN,
                    invocations_24h=490,
                    wraps="cross-region",
                ),
                _make_app_profile(
                    "analytics-bot",
                    ANALYTICS_BOT_ARN,
                    invocations_24h=120,
                    wraps="cross-region",
                ),
            ],
        )
        out = compose_shared_quota_warning(resolved, snapshot)

        lines = out.splitlines()
        assert lines[0] == "⚠  Other traffic also counts against this limit:"
        # Last line is the follow-up hint; starts with 3 spaces (matches the
        # composer's indent).
        assert lines[-1].startswith("   Check check_quota_utilization")
        assert lines[-1].endswith("combined usage.")
        # Bullets are indented 3 spaces and start with "- ".
        bullet_lines = [line for line in lines if line.startswith("   - ")]
        assert len(bullet_lines) == 2
        # Both co-tenants are represented.
        joined = "\n".join(bullet_lines)
        assert "us.anthropic.claude-sonnet-4-6-v1:0" in joined
        assert "analytics-bot" in joined
