"""
Unit tests for the profile-aware behaviour of
``agent.get_bedrock_model_invocation_metrics`` (Task 8.5 in
``.kiro/specs/per-profile-metrics/tasks.md``).

Covers six scenarios from the per-profile-metrics spec (Req 4.5, 4.6, 7.1,
7.2, 7.3; Design Component 4):

1. Bare model ID path is unchanged. The output keeps the model-grain header
   lines (``Model ID: <bare>``) and does not leak an
   ``Application_Profile:`` or ``Inference Profile:`` label into the header
   (Req 7.2).
2. ``Application_Profile`` ref queries CloudWatch with the ARN as the
   ``ModelId`` dimension, and the header carries the friendly name + ARN +
   underlying model id (Req 4.2, 7.1).
3. ``System_Defined_Profile`` header shows the ``Inference Profile:`` label
   alongside the underlying bare ``Model ID:`` line (Req 7.1).
4. Drill-down hint echoes the supplied Profile_Ref verbatim so follow-up
   suggestions preserve context rather than collapsing to the base model
   ID (Req 7.3).
5. Ambiguous ref short-circuits with the candidate list and makes **no**
   CloudWatch call (Req 4.5).
6. Unresolved ref points the user at ``get_customer_profile`` and makes
   **no** CloudWatch call (Req 4.6).

The import / mock pattern mirrors
``src/tests/unit/test_check_quota_utilization_profile_aware.py`` verbatim:
identity ``tool`` decorator, stubbed sibling modules, boto3 SSM mocked at
``agent`` load time, autouse ``reset_snapshot_cache`` fixture, Snapshot cache
slot seeded directly so DynamoDB is never touched, and
``agent._get_metrics_batch`` patched so CloudWatch is never actually called.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


import importlib
import helpers.snapshot as helpers_snapshot

_tools_metrics_mod = importlib.import_module("tools.get_bedrock_model_invocation_metrics")
get_bedrock_model_invocation_metrics = _tools_metrics_mod.get_bedrock_model_invocation_metrics


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
    timestamp keeps tests deterministic — the metrics tool renders the peak
    timestamp so it needs to be a real ``datetime``.
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
# Test 1 — Bare model ID path is unchanged (back-compat for drilldown/*)
# ===========================================================================


class TestBareModelIdPathUnchanged:
    """Calling the tool with a bare friendly alias must keep the model-grain
    header lines intact — ``Model ID:`` references the bare base model ID
    and neither ``Application_Profile:`` nor ``Inference Profile:`` shows up
    in the header (those labels are only emitted on the system_profile /
    application_profile branches).

    Back-compat guard for callers that pass a bare model ID.
    """

    def test_bare_alias_header_has_no_profile_label(self):
        # Snapshot with a single active profile — nothing for the branches
        # we're verifying to trigger on.
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

        with patch(
                    "helpers.profile_resolution.resolve_model_id", return_value=BASE_MODEL
                ), \
                patch.object(
                    _tools_metrics_mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(
                    _tools_metrics_mod, "_get_metrics_batch", return_value=metrics
                ):
            output = get_bedrock_model_invocation_metrics(
                model_id="claude sonnet 4.6",
                region=REGION,
                hours_back=1,
            )

        # Model-grain header lines present.
        assert "Bedrock Model Metrics for Claude Sonnet 4.6" in output
        assert f"Model ID: {BASE_MODEL}" in output

        # Profile-specific header labels must NOT leak into the model-grain
        # output — these are the labels emitted by the system_profile /
        # application_profile branches.
        assert "Application_Profile:" not in output
        assert "Application_Profile ARN:" not in output
        # ``Inference Profile:`` is the system_profile-only label line — must
        # not appear on a bare model-grain call.
        assert "Inference Profile:" not in output


# ===========================================================================
# Test 2 — Application_Profile queries CloudWatch with the ARN dimension
# ===========================================================================


class TestApplicationProfileUsesArnAsCwModelId:
    """When the resolver returns ``kind="application_profile"``, the tool must
    pass the ARN to ``_get_metrics_batch`` as ``cw_model_id`` (the CloudWatch
    ``ModelId`` dimension value), and the header must carry the friendly
    name, the ARN, and the underlying foundation model ID.
    """

    def test_marketing_bot_calls_cloudwatch_with_arn_and_renders_header(self):
        snapshot = _snapshot([
            _model_entry(
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
            invocations=[9.0],
            input_tokens=[2_800.0],
            output_tokens=[420.0],
        )

        metrics_mock = MagicMock(return_value=metrics)

        with patch.object(
                    _tools_metrics_mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(_tools_metrics_mod, "_get_metrics_batch", metrics_mock):
            output = get_bedrock_model_invocation_metrics(
                model_id="marketing-bot",
                region=REGION,
                hours_back=1,
            )

        # CloudWatch was called with the ARN as the cw_model_id dimension.
        assert metrics_mock.called
        call = metrics_mock.call_args
        # ``_get_metrics_batch(region, cw_model_id, start_time, end_time, period)``
        # — cw_model_id is the second positional arg.
        positional_args = call.args
        assert positional_args[1] == MKT_BOT_ARN, (
            f"Expected CloudWatch query against ARN dimension "
            f"{MKT_BOT_ARN!r}, got {positional_args[1]!r}"
        )

        # Header must carry the friendly name, the ARN, and the underlying
        # foundation model ID.
        assert "Application_Profile: marketing-bot" in output
        assert f"Application_Profile ARN: {MKT_BOT_ARN}" in output
        assert f"Underlying Model ID: {BASE_MODEL}" in output


# ===========================================================================
# Test 3 — System_Defined_Profile header shows the Inference_Profile label
# ===========================================================================


class TestSystemProfileHeaderShowsInferenceProfileLabel:
    """When the resolver returns ``kind="system_profile"``, the tool's header
    must carry BOTH the underlying base ``Model ID:`` line AND the
    ``Inference Profile:`` label with the prefixed profile ID.
    """

    def test_us_prefix_header_has_inference_profile_label(self):
        # Snapshot is irrelevant for the system_profile branch — prefix is
        # self-describing.
        helpers_snapshot._snapshot_cache = _snapshot([])

        metrics = _fake_metrics_batch(
            invocations=[142.0],
            input_tokens=[58_200.0],
            output_tokens=[4_100.0],
        )

        with patch.object(
                    _tools_metrics_mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(
                    _tools_metrics_mod, "_get_metrics_batch", return_value=metrics
                ):
            output = get_bedrock_model_invocation_metrics(
                model_id=US_CW_ID,
                region=REGION,
                hours_back=1,
            )

        # Header shows both the underlying base model id line and the
        # Inference_Profile label with the prefixed id.
        assert f"Model ID: {BASE_MODEL}" in output
        assert f"Inference Profile: {US_CW_ID}" in output


# ===========================================================================
# Test 4 — Drill-down hint echoes the supplied Profile_Ref (Req 7.3)
# ===========================================================================


class TestDrilldownHintEchoesProfileRef:
    """When the output is rendered at coarse granularity (period > 60s), the
    tool emits a drill-down hint pointing the user at a narrower window. The
    hint must echo the **original** Profile_Ref the caller supplied so the
    follow-up stays on the same Inference_Profile rather than collapsing
    back to model grain.
    """

    def test_application_profile_drilldown_hint_preserves_original_ref(self):
        snapshot = _snapshot([
            _model_entry(
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

        # Hours_back=168 → 7 days → ``_calculate_period`` returns
        # (period=300, "5-minute"). period > 60 ⇒ drill-down hint is emitted.
        metrics = _fake_metrics_batch(invocations=[9.0])

        with patch.object(
                    _tools_metrics_mod, "get_model_info",
                    return_value={"name": "Claude Sonnet 4.6"},
                ), \
                patch.object(
                    _tools_metrics_mod, "_get_metrics_batch", return_value=metrics
                ):
            output = get_bedrock_model_invocation_metrics(
                model_id="marketing-bot",
                region=REGION,
                hours_back=168,
            )

        # Drill-down hint landmark.
        assert "For minute-level detail around the peak" in output
        # The hint must echo the **original** Profile_Ref string (``'marketing-bot'``)
        # — not the resolved ARN, not the underlying base model id. That is
        # the contract in Req 7.3 / Task 8.3 and keeps the follow-up on the
        # same Inference_Profile.
        assert "model_id='marketing-bot'" in output
        # Negative check: the hint must not swap in the base model id or the
        # ARN as the echoed Profile_Ref.
        assert f"model_id='{BASE_MODEL}'" not in output
        assert f"model_id='{MKT_BOT_ARN}'" not in output


# ===========================================================================
# Test 5 — Ambiguous ref short-circuits (Req 4.5)
# ===========================================================================


class TestAmbiguousRefShortCircuits:
    """≥ 2 Application_Profiles share a name (case-insensitive) → resolver
    returns ``kind="ambiguous"``; the tool emits the candidate list and
    never calls CloudWatch.
    """

    def test_two_shared_bot_matches_short_circuit(self):
        # Two Application_Profiles named "shared-bot" under different parent
        # base models / regions — resolver returns ambiguous.
        snapshot = _snapshot([
            _model_entry(
                model_id=BASE_MODEL,
                display_name="Claude Sonnet 4.6",
                app_profiles=[
                    _app_profile(
                        "shared-bot",
                        SHARED_ARN_A,
                        invocations_24h=10,
                        wraps="cross-region",
                    ),
                ],
            ),
            _model_entry(
                model_id="amazon.nova-pro-v1:0",
                display_name="Nova Pro",
                app_profiles=[
                    _app_profile(
                        "shared-bot",
                        SHARED_ARN_B,
                        invocations_24h=10,
                        wraps="cross-region",
                    ),
                ],
            ),
        ])
        helpers_snapshot._snapshot_cache = snapshot

        metrics_mock = MagicMock()

        with patch.object(_tools_metrics_mod, "_get_metrics_batch", metrics_mock):
            output = get_bedrock_model_invocation_metrics(
                model_id="shared-bot",
                region=REGION,
                hours_back=1,
            )

        # Disambiguation message landmark.
        assert "Multiple inference profiles match" in output
        assert "shared-bot" in output
        # Both candidate labels must be present with their distinguishing
        # region + parent-base-model qualifiers.
        assert "us-east-1" in output
        assert BASE_MODEL in output
        assert "us-west-2" in output
        assert "amazon.nova-pro-v1:0" in output

        # CRITICAL: CloudWatch was not called.
        assert not metrics_mock.called


# ===========================================================================
# Test 6 — Unresolved ref points the user to get_customer_profile (Req 4.6)
# ===========================================================================


class TestUnresolvedRefPointsToGetCustomerProfile:
    """Ref that matches nothing (no ARN, no prefix, no name, no alias) →
    resolver returns ``kind="unresolved"`` and the tool surfaces a message
    recommending ``get_customer_profile``. CloudWatch must not be called.
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

        # ``resolve_model_id`` must return falsy so branch 5 (bare alias) in
        # the resolver fails, letting the ref fall through to branch 6
        # ("unresolved"). The shim in ``sys.modules["models"]`` is a
        # MagicMock — without this patch it would return a truthy MagicMock
        # and branch 5 would claim a match.
        unresolved_result = {
            "kind": "unresolved",
            "cw_model_id": None,
            "base_model_id": None,
            "application_profile_name": None,
            "application_profile_arn": None,
            "underlying_quota_scope": None,
            "candidates": [],
            "unresolved_ref": "nonexistent-xyz",
        }
        with patch.object(_tools_metrics_mod, "resolve_profile_ref", return_value=unresolved_result), \
                patch.object(_tools_metrics_mod, "_get_metrics_batch", metrics_mock):
            output = get_bedrock_model_invocation_metrics(
                model_id="nonexistent-xyz",
                region=REGION,
                hours_back=1,
            )

        # Unresolved-ref message + recommendation.
        assert "Could not resolve" in output
        assert "nonexistent-xyz" in output
        assert "get_customer_profile" in output

        # CRITICAL: CloudWatch was not called.
        assert not metrics_mock.called
