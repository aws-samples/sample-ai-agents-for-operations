"""
Unit tests for the profile-aware behaviour of ``agent.check_quota_utilization``
(Task 7.9 in ``.kiro/specs/per-profile-metrics/tasks.md``).

Pins seven scenarios from the per-profile-metrics spec:

1. Bare model ID path is unchanged — model-grain output, no disclosure line
   when only one profile is active (Req 6.1).
2. ``System_Defined_Profile`` ref (``us.`` / ``eu.`` / ``ap.`` / ``jp.`` /
   ``global.`` prefix) fetches the cross-region quota, not the on-demand
   quota (Req 6.2).
3. ``Application_Profile`` ref renders Template D — ``Shared with other
   callers of …`` label plus the ⚠ warning block listing co-tenants
   (Req 5.3, 5.4, 6.3).
4. ``Application_Profile`` whose wrapping target is absent from the Snapshot
   (``underlying_quota_scope = None``) returns raw metrics without
   percentages and explains that the underlying quota could not be resolved
   (Req 5.5).
5. Bare model ID with ≥ 2 Active_Inference_Profiles on the same model
   triggers the disclosure line at the end of the model-grain answer
   (Req 1.2, 6.4).
6. Ambiguous ref short-circuits with the candidate list and makes **no**
   CloudWatch call (Req 4.5).
7. Unresolved ref points the user at ``get_customer_profile`` (Req 4.6).

Import / mock pattern mirrors
``src/tests/unit/test_list_active_inference_profiles.py`` and
``src/tests/unit/test_list_active_bedrock_models_by_profile.py``:

- ``strands`` is stubbed with an identity ``tool`` decorator so the
  ``@tool``-decorated ``check_quota_utilization`` is directly callable.
- ``bedrock_agentcore`` and ``models`` are stubbed as ``MagicMock``.
- ``agent.py`` is (re-)imported under a stubbed ``boto3.client`` so the SSM
  parameter reads at module load time never hit AWS.
- The Snapshot cache slot (``helpers_snapshot._snapshot_cache``) is seeded directly by
  each test so ``get_snapshot_cached()`` never touches DynamoDB.
- ``agent.resolve_model_id`` and ``agent.get_model_info`` are patched where
  the tool's control flow branches on their return values.
- ``agent._get_metrics_batch`` and ``agent._fetch_rpm_tpm_quotas_for_utilization``
  are patched so CloudWatch + Service Quotas are never actually called.
"""

import importlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import helpers.snapshot as helpers_snapshot

_mod = importlib.import_module("tools.check_quota_utilization")
check_quota_utilization = _mod.check_quota_utilization


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REGION = "us-east-1"
BASE_MODEL = "anthropic.claude-sonnet-4-6-v1:0"
US_CW_ID = "us.anthropic.claude-sonnet-4-6-v1:0"

MKT_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/mkt-bot-abc123"
)
ANALYTICS_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/analytics-bot-def456"
)
ORPHAN_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/orphan-ghi789"
)
SHARED_ARN_A = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/shared-a-aaa"
)
SHARED_ARN_B = (
    "arn:aws:bedrock:us-west-2:123456789012:"
    "application-inference-profile/shared-b-bbb"
)


# ---------------------------------------------------------------------------
# Snapshot fixture helpers — schema matches customer-profile/design.md
# ---------------------------------------------------------------------------


def _access_pattern(
    cw_model_id: str,
    pattern_type: str = "cross-region-geo",
    geography: str | None = "us",
    invocations_24h: int = 100,
) -> dict:
    return {
        "pattern_type": pattern_type,
        "cw_model_id": cw_model_id,
        "regions": [REGION],
        "geography": geography,
        "quota_limits": {"rpm_limit": 400, "tpm_limit": 400_000},
        "usage_summary": {"invocations_24h": invocations_24h},
    }


def _app_profile(
    name: str,
    arn: str,
    *,
    invocations_24h: int = 0,
    wraps: str | None = "cross-region",
) -> dict:
    entry: dict = {
        "name": name,
        "arn": arn,
        "tags": {},
        "invocations_24h": invocations_24h,
    }
    if wraps is not None:
        entry["wraps"] = wraps
    return entry


def _model_entry(
    *,
    model_id: str = BASE_MODEL,
    display_name: str = "Claude Sonnet 4.6",
    access_patterns: list[dict] | None = None,
    app_profiles: list[dict] | None = None,
) -> dict:
    return {
        "model_id": model_id,
        "provider": "Anthropic",
        "display_name": display_name,
        "active_patterns": access_patterns or [],
        "app_profiles": app_profiles or [],
    }


def _snapshot(models: list[dict]) -> dict:
    return {"PK": "customer-profile", "SK": "latest", "models": models}


# ---------------------------------------------------------------------------
# CloudWatch metrics factory — returns the shape ``_get_metrics_batch`` does
# ---------------------------------------------------------------------------


def _fake_metrics_batch(
    *,
    invocations: list[float] | None = None,
    input_tokens: list[float] | None = None,
    output_tokens: list[float] | None = None,
    latency: list[float] | None = None,
    throttles: list[float] | None = None,
    client_errors: list[float] | None = None,
    server_errors: list[float] | None = None,
) -> dict:
    """Build a metrics dict with the shape ``_get_metrics_batch`` returns.

    Each metric is a list of ``(timestamp, value)`` tuples. Default fixed
    timestamp keeps tests deterministic — ``check_quota_utilization`` renders
    the peak timestamp into the output so we need it to be a real
    ``datetime``.
    """
    now = datetime(2025, 1, 1, 12, 0, 0)

    def _pairs(values: list[float] | None) -> list[tuple[datetime, float]]:
        return [(now, v) for v in (values or [])]

    return {
        "invocations": _pairs(invocations),
        "input_tokens": _pairs(input_tokens),
        "output_tokens": _pairs(output_tokens),
        "latency": _pairs(latency),
        "throttles": _pairs(throttles),
        "client_errors": _pairs(client_errors),
        "server_errors": _pairs(server_errors),
    }


# ---------------------------------------------------------------------------
# Autouse cleanup so tests never leak state between each other.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_snapshot_cache():
    helpers_snapshot._snapshot_cache = None
    yield
    helpers_snapshot._snapshot_cache = None


# ===========================================================================
# Test 1 — Bare model ID path is unchanged (back-compat guard, Req 6.1)
# ===========================================================================


class TestBareModelIdPathUnchanged:
    """The existing model-grain flow must keep its shape when called with a
    bare model ID or friendly alias.

    - Model-grain header lines appear (no Template D "— this application
      alone:" suffix).
    - No disclosure line because the Snapshot has only one active profile.
    """

    def test_bare_alias_renders_model_grain_output(self):
        # Snapshot has the model but with only ONE active profile — the
        # disclosure trigger (2+ active profiles) must not fire.
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(
                        BASE_MODEL,
                        pattern_type="on-demand",
                        geography=None,
                        invocations_24h=920,
                    ),
                ],
            ),
        ])
        helpers_snapshot._snapshot_cache = snapshot

        metrics = _fake_metrics_batch(
            invocations=[18.0],
            input_tokens=[4_200.0],
            output_tokens=[610.0],
            latency=[120.0],
        )

        # Mock the inputs that would otherwise hit AWS / the real resolvers.
        with patch.object(_mod, "resolve_profile_ref", return_value={"kind": "model", "cw_model_id": BASE_MODEL, "base_model_id": BASE_MODEL, "application_profile_name": None, "application_profile_arn": None, "underlying_quota_scope": "on-demand", "candidates": [], "unresolved_ref": None}), \
                patch.object(
                    _mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(_mod, "_get_metrics_batch", return_value=metrics), \
                patch.object(
                    _mod, "_fetch_rpm_tpm_quotas_for_utilization",
                    return_value=(400, 400_000, "Anthropic Claude Sonnet 4.6"),
                ):
            output = check_quota_utilization(
                model_id="claude sonnet 4.6",
                region=REGION,
                hours_back=1,
            )

        # Model-grain headers.
        assert "Quota Utilization Analysis for Claude Sonnet 4.6" in output
        assert f"Model ID: {BASE_MODEL}" in output
        assert f"Region: {REGION}" in output
        # On-demand prefix → no cross-region prefix inferred.
        assert "Inference Type: On-demand (single region)" in output
        # Model-grain wording — no Template D "this application alone" suffix.
        assert "Requests Per Minute (RPM):" in output
        assert "— this application alone:" not in output
        assert "Tokens Per Minute (TPM):" in output
        # Single active profile → disclosure line must NOT appear.
        assert "Note: usage for this model is split across" not in output


# ===========================================================================
# Test 2 — System_Defined_Profile ref fetches the cross-region quota (Req 6.2)
# ===========================================================================


class TestSystemProfileUsesCrossRegionQuota:
    """When the resolver returns ``kind="system_profile"``,
    ``check_quota_utilization`` must call the quota fetcher with
    ``prefer_cross_region=True`` and render the cross-region values.
    """

    def test_us_prefix_resolves_to_cross_region_quota(self):
        # Snapshot doesn't matter for the system_profile branch — the prefix
        # is self-describing.
        helpers_snapshot._snapshot_cache = _snapshot([])

        metrics = _fake_metrics_batch(
            invocations=[142.0],
            input_tokens=[58_200.0],
            output_tokens=[4_100.0],
        )

        # Return very different RPM/TPM values depending on whether the
        # caller asked for cross-region or on-demand. The test then asserts
        # the cross-region values are what show up in the output.
        CROSS_REGION_RPM = 400
        CROSS_REGION_TPM = 400_000
        ON_DEMAND_RPM = 50
        ON_DEMAND_TPM = 50_000

        def _fake_quota_fetch(region, display_name, *, prefer_cross_region):
            if prefer_cross_region:
                return (
                    CROSS_REGION_RPM,
                    CROSS_REGION_TPM,
                    "Cross-region Claude Sonnet 4.6",
                )
            return (
                ON_DEMAND_RPM,
                ON_DEMAND_TPM,
                "On-demand Claude Sonnet 4.6",
            )

        quota_mock = MagicMock(side_effect=_fake_quota_fetch)

        with patch.object(_mod, "resolve_profile_ref", return_value={"kind": "system_profile", "cw_model_id": US_CW_ID, "base_model_id": BASE_MODEL, "application_profile_name": None, "application_profile_arn": None, "underlying_quota_scope": "cross-region", "candidates": [], "unresolved_ref": None}), \
                patch.object(
                    _mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(_mod, "_get_metrics_batch", return_value=metrics), \
                patch.object(
                    _mod, "_fetch_rpm_tpm_quotas_for_utilization",
                    quota_mock,
                ):
            output = check_quota_utilization(
                model_id=US_CW_ID,
                region=REGION,
                hours_back=1,
            )

        # The quota fetcher must have been asked for cross-region values.
        assert quota_mock.called
        # Second arg is positional region, but prefer_cross_region is kwarg.
        call = quota_mock.call_args
        assert call.kwargs.get("prefer_cross_region") is True, call

        # The cross-region RPM/TPM values must appear in the rendered output.
        assert f"Quota Limit: {CROSS_REGION_RPM} RPM" in output
        assert f"Quota Limit: {CROSS_REGION_TPM} TPM" in output
        # And the on-demand values must NOT appear — if they did the tool
        # picked the wrong scope.
        assert f"Quota Limit: {ON_DEMAND_RPM} RPM" not in output
        assert f"Quota Limit: {ON_DEMAND_TPM} TPM" not in output

        # Cross-region inference type header.
        assert "Inference Type: Cross-region" in output
        # The Model ID line carries the prefixed id. The agent only adds a
        # separate "CloudWatch Model ID:" line when the CW id differs from
        # the Model ID — for the system_profile branch they are the same,
        # so we verify via the Model ID line itself.
        assert f"Model ID: {US_CW_ID}" in output


# ===========================================================================
# Test 3 — Application_Profile renders Template D with shared-quota warning
# (Req 5.3, 5.4, 6.3)
# ===========================================================================


class TestApplicationProfileRendersTemplateD:
    """Application_Profile ref → Template D layout + co-tenant warning block."""

    def test_marketing_bot_template_d_with_shared_warning(self):
        # Snapshot: one model with
        #   - an AccessPattern on us.<base> (direct cross-region traffic) — co-tenant
        #   - the queried app_profile "marketing-bot"
        #   - another app_profile "analytics-bot" — co-tenant
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(
                        US_CW_ID,
                        pattern_type="cross-region-geo",
                        geography="us",
                        invocations_24h=11_437,
                    ),
                ],
                app_profiles=[
                    _app_profile(
                        "marketing-bot",
                        MKT_BOT_ARN,
                        invocations_24h=490,
                        wraps="cross-region",
                    ),
                    _app_profile(
                        "analytics-bot",
                        ANALYTICS_BOT_ARN,
                        invocations_24h=120,
                        wraps="cross-region",
                    ),
                ],
            ),
        ])
        helpers_snapshot._snapshot_cache = snapshot

        metrics = _fake_metrics_batch(
            invocations=[9.0],
            input_tokens=[2_800.0],
            output_tokens=[420.0],
        )

        with patch.object(
                    _mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(_mod, "_get_metrics_batch", return_value=metrics), \
                patch.object(
                    _mod, "_fetch_rpm_tpm_quotas_for_utilization",
                    return_value=(400, 400_000, "Cross-region Claude Sonnet 4.6"),
                ):
            output = check_quota_utilization(
                model_id="marketing-bot",
                region=REGION,
                hours_back=1,
            )

        # Template D header landmarks.
        assert "Quota Utilization Analysis for marketing-bot" in output
        assert f"Application_Profile ARN: {MKT_BOT_ARN}" in output
        assert (
            f"Underlying CW_Model_ID: {US_CW_ID} (cross-region-geo)"
            in output
        )
        assert "Inference Type: Cross-region (shared quota)" in output

        # Per-app body phrasing.
        assert "Requests Per Minute (RPM) — this application alone:" in output
        assert (
            f"(shared with other callers of {US_CW_ID})" in output
        )
        assert "Peak Utilization (this app):" in output

        # Shared-quota warning block with co-tenants named.
        assert "⚠  Other traffic also counts against this limit:" in output
        # Direct cross-region traffic from the AccessPattern.
        assert f"{US_CW_ID} (direct cross-region traffic)" in output
        # Other Application_Profile sharing the underlying quota.
        assert "analytics-bot (application-inference-profile)" in output
        # The resolved profile itself must NOT appear in the co-tenant list.
        assert "marketing-bot (application-inference-profile)" not in output


# ===========================================================================
# Test 4 — Application_Profile with missing wrapping target (Req 5.5)
# ===========================================================================


class TestApplicationProfileMissingWrappingTarget:
    """When ``wraps`` is absent the resolver produces
    ``underlying_quota_scope=None`` and the tool must emit raw metrics without
    percentages plus the "could not be resolved" note.
    """

    def test_orphan_profile_returns_raw_metrics_and_explanation(self):
        # Snapshot: app profile without a ``wraps`` field → scope=None.
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[],
                app_profiles=[
                    _app_profile(
                        "orphan-bot",
                        ORPHAN_ARN,
                        invocations_24h=42,
                        wraps=None,  # wraps field omitted
                    ),
                ],
            ),
        ])
        helpers_snapshot._snapshot_cache = snapshot

        metrics = _fake_metrics_batch(
            invocations=[7.0],
            input_tokens=[1_500.0],
            output_tokens=[300.0],
        )

        # Quota fetch should NOT be called when scope is None — but we still
        # patch it so a stray call doesn't reach AWS. The test below asserts
        # the mock was not invoked.
        quota_mock = MagicMock(return_value=(400, 400_000, "unused"))

        with patch.object(
                    _mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(_mod, "_get_metrics_batch", return_value=metrics), \
                patch.object(
                    _mod, "_fetch_rpm_tpm_quotas_for_utilization",
                    quota_mock,
                ):
            output = check_quota_utilization(
                model_id="orphan-bot",
                region=REGION,
                hours_back=1,
            )

        # The explanatory note is emitted.
        assert (
            "Note: The underlying quota for this application profile "
            "could not be resolved"
            in output
        )

        # Raw RPM metrics are shown but without a percentage or shared label.
        assert "Requests Per Minute (RPM):" in output
        # No "— this application alone:" suffix (that's only when scope is set).
        assert "— this application alone:" not in output
        # No Peak Utilization line (no percentage).
        assert "Peak Utilization (this app):" not in output
        # No shared-quota label.
        assert "(shared with other callers of" not in output
        # No shared-quota warning block — that also only renders when scope set.
        assert "⚠  Other traffic also counts against this limit:" not in output

        # Quota fetch must not have been called (scope is None).
        assert not quota_mock.called


# ===========================================================================
# Test 5 — Multi-profile disclosure appended to model-grain answer
# (Req 1.2, 6.4)
# ===========================================================================


class TestMultiProfileDisclosureOnModelGrain:
    """Bare model ID + Snapshot with 2+ active profiles (invocations > 0) →
    model-grain output + the one-line disclosure appended at the end.
    """

    def test_disclosure_line_appended(self):
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(
                        BASE_MODEL,
                        pattern_type="on-demand",
                        geography=None,
                        invocations_24h=920,
                    ),
                    _access_pattern(
                        US_CW_ID,
                        pattern_type="cross-region-geo",
                        geography="us",
                        invocations_24h=11_437,
                    ),
                ],
                app_profiles=[
                    _app_profile(
                        "marketing-bot",
                        MKT_BOT_ARN,
                        invocations_24h=490,
                        wraps="cross-region",
                    ),
                ],
            ),
        ])
        helpers_snapshot._snapshot_cache = snapshot

        metrics = _fake_metrics_batch(
            invocations=[142.0],
            input_tokens=[58_200.0],
            output_tokens=[4_100.0],
        )

        with patch.object(_mod, "resolve_profile_ref", return_value={"kind": "model", "cw_model_id": BASE_MODEL, "base_model_id": BASE_MODEL, "application_profile_name": None, "application_profile_arn": None, "underlying_quota_scope": "on-demand", "candidates": [], "unresolved_ref": None}), \
                patch.object(
                    _mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(_mod, "_get_metrics_batch", return_value=metrics), \
                patch.object(
                    _mod, "_fetch_rpm_tpm_quotas_for_utilization",
                    return_value=(400, 400_000, "Anthropic Claude Sonnet 4.6"),
                ):
            output = check_quota_utilization(
                model_id="claude sonnet 4.6",
                region=REGION,
                hours_back=1,
            )

        # Model-grain body still present.
        assert "Quota Utilization Analysis for Claude Sonnet 4.6" in output
        assert f"Model ID: {BASE_MODEL}" in output

        # Disclosure line appears near the end, names all 3 active profiles,
        # and invites a breakdown.
        assert "Note: usage for this model is split across" in output
        assert "3" in output.split("split across", 1)[1]  # "3 active profiles"
        assert BASE_MODEL in output
        assert US_CW_ID in output
        assert "marketing-bot" in output
        assert "Ask for a breakdown to see per-profile metrics." in output


# ===========================================================================
# Test 6 — Ambiguous ref short-circuits with candidates, no CloudWatch call
# (Req 4.5)
# ===========================================================================


class TestAmbiguousRefShortCircuits:
    """≥ 2 App_Profiles share a name (case-insensitive) → resolver returns
    ``kind="ambiguous"``; the tool emits the candidate list and never calls
    CloudWatch or the quota fetcher.
    """

    def test_two_shared_bot_matches_short_circuit(self):
        # Two Application_Profiles named "shared-bot" on different parent
        # models — resolver must return ambiguous.
        snapshot = _snapshot([
            _model_entry(
                model_id=BASE_MODEL,
                display_name="Claude Sonnet 4.6",
                app_profiles=[
                    _app_profile("shared-bot", SHARED_ARN_A,
                                 invocations_24h=10, wraps="cross-region"),
                ],
            ),
            _model_entry(
                model_id="amazon.nova-pro-v1:0",
                display_name="Nova Pro",
                app_profiles=[
                    _app_profile("shared-bot", SHARED_ARN_B,
                                 invocations_24h=10, wraps="cross-region"),
                ],
            ),
        ])
        helpers_snapshot._snapshot_cache = snapshot

        metrics_mock = MagicMock()
        quota_mock = MagicMock()

        with patch("tools.check_quota_utilization._get_metrics_batch", metrics_mock), \
                patch.object(
                    _mod, "_fetch_rpm_tpm_quotas_for_utilization",
                    quota_mock,
                ):
            output = check_quota_utilization(
                model_id="shared-bot",
                region=REGION,
                hours_back=1,
            )

        # Disambiguation message landmark.
        assert "Multiple inference profiles match" in output
        assert "shared-bot" in output
        # Both candidate labels should be present with their distinguishing
        # qualifiers — candidate labels use the form
        # ``"<name> (<region>, <parent_base_id>)"`` (not the full ARN), so
        # assert on the region + base-model qualifiers that set them apart.
        assert "us-east-1" in output
        assert BASE_MODEL in output
        assert "us-west-2" in output
        assert "amazon.nova-pro-v1:0" in output

        # CRITICAL: neither CloudWatch nor the quota fetcher was touched.
        assert not metrics_mock.called
        assert not quota_mock.called


# ===========================================================================
# Test 7 — Unresolved ref points the user to get_customer_profile (Req 4.6)
# ===========================================================================


class TestUnresolvedRefPointsToGetCustomerProfile:
    """Ref that matches nothing (no ARN, no prefix, no name, no alias) →
    resolver returns ``kind="unresolved"`` and the tool surfaces a message
    recommending ``get_customer_profile``.
    """

    def test_unknown_ref_returns_unresolved_message(self):
        # Snapshot contains no profile with this name.
        snapshot = _snapshot([
            _model_entry(
                app_profiles=[
                    _app_profile(
                        "known-bot",
                        MKT_BOT_ARN,
                        invocations_24h=10,
                        wraps="cross-region",
                    ),
                ],
            ),
        ])
        helpers_snapshot._snapshot_cache = snapshot

        metrics_mock = MagicMock()
        quota_mock = MagicMock()

        # ``resolve_model_id`` must return falsy so branch 5 (bare alias) in
        # the resolver fails, letting the ref fall through to branch 6
        # ("unresolved"). The shim in ``sys.modules["models"]`` is a
        # MagicMock — without this patch it would return a truthy MagicMock
        # and branch 5 would claim a match.
        with patch.object(_mod, "resolve_profile_ref", return_value={"kind": "unresolved", "cw_model_id": None, "base_model_id": None, "application_profile_name": None, "application_profile_arn": None, "underlying_quota_scope": None, "candidates": [], "unresolved_ref": "nonexistent-profile-xyz"}), \
                patch("tools.check_quota_utilization._get_metrics_batch", metrics_mock), \
                patch.object(
                    _mod, "_fetch_rpm_tpm_quotas_for_utilization",
                    quota_mock,
                ):
            output = check_quota_utilization(
                model_id="nonexistent-profile-xyz",
                region=REGION,
                hours_back=1,
            )

        # Unresolved-ref message + recommendation.
        assert "Could not resolve" in output
        assert "nonexistent-profile-xyz" in output
        assert "get_customer_profile" in output

        # No CloudWatch or quota fetch.
        assert not metrics_mock.called
        assert not quota_mock.called
