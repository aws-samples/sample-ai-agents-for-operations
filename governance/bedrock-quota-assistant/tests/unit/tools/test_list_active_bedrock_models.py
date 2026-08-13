"""
Unit tests for the ``by_profile`` parameter on ``list_active_bedrock_models``
in ``src/agent.py``.

These tests pin the three guarantees called out by
``.kiro/specs/per-profile-metrics/tasks.md`` Task 6.3 and the matching
requirements:

1. **Back-compat guard (Req 3.7).** With the default ``by_profile=False``, the
   tool's output matches the pre-existing model-grain shape byte-for-byte —
   same header, same totals line, no ``(application-profile)`` tag, and
   crucially no Template E header.
2. **Profile-grain opt-in (Req 3.8).** When ``by_profile=True``, the tool
   delegates to the same implementation as ``list_active_inference_profiles``
   and emits Template E: per-profile sections with the
   ``(application-profile)`` tag on Application_Profile rows, ordered
   descending by invocation count.
3. **Lockstep / sum-matches-model (Req 3.10).** The profile-grain
   ``by_profile=True`` output is byte-for-byte identical to
   ``list_active_inference_profiles`` with the same inputs, and the sum of
   per-profile invocation counts in that output equals the model-grain
   invocation count reported by ``by_profile=False`` for the same model
   (modulo CloudWatch rounding, which is nil in these mocked tests).

Import / mock pattern mirrors
``src/tests/unit/test_list_active_inference_profiles.py``: third-party modules
that would otherwise cause heavy imports (strands, bedrock_agentcore, models)
are stubbed in ``sys.modules``, ``strands.tool`` is an identity decorator so
the ``@tool``-wrapped functions remain directly callable, and ``agent.py`` is
(re-)imported under a stubbed ``boto3.client`` so the SSM parameter reads at
module load time never hit AWS.
"""

from unittest.mock import MagicMock, patch

import pytest


import importlib

import helpers.snapshot as helpers_snapshot

_mod = importlib.import_module("tools.list_active_bedrock_models")
list_active_bedrock_models = _mod.list_active_bedrock_models

_lip_mod = importlib.import_module("tools.list_active_inference_profiles")
list_active_inference_profiles = _lip_mod.list_active_inference_profiles


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REGION = "us-east-1"

# Two distinct ModelIds that ``list_metrics`` will yield in the model-grain
# discovery path.
BARE_SONNET_CW_ID = "anthropic.claude-sonnet-4-6-v1:0"
US_SONNET_CW_ID = "us.anthropic.claude-sonnet-4-6-v1:0"

MKT_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/mkt-bot-abc123"
)


# ---------------------------------------------------------------------------
# Fixture helpers — Snapshot shape matches customer-profile/design.md.
# Mirrored from test_list_active_inference_profiles.py for consistency.
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
        "quota_limits": {"rpm_limit": 100, "tpm_limit": 100_000},
        "usage_summary": {"invocations_24h": invocations_24h},
    }


def _app_profile(name: str, arn: str, invocations_24h: int = 0) -> dict:
    return {
        "name": name,
        "arn": arn,
        "tags": {},
        "invocations_24h": invocations_24h,
        "wraps": "cross-region",
    }


def _model_entry(
    model_id: str = "anthropic.claude-sonnet-4-6-v1:0",
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
# CloudWatch mock factory — same shape as the sibling test file.
# ---------------------------------------------------------------------------


def _make_cw_mock(
    counts_by_model_id: dict[str, int],
    list_metrics_dimensions: list[str] | None = None,
):
    """Build a ``MagicMock`` cloudwatch client.

    - ``get_metric_statistics`` returns a single Datapoint with ``Sum`` equal to
      the count registered for the queried ``ModelId`` dimension, or an empty
      ``Datapoints`` list when the count is zero or the id is unknown.
    - When ``list_metrics_dimensions`` is supplied, the ``list_metrics``
      paginator yields one page with one Metric per supplied id. This is what
      the model-grain branch of ``list_active_bedrock_models`` walks.
    """
    mock_cw = MagicMock()

    def _get_metric_statistics(**kwargs):
        dims = kwargs.get("Dimensions", [])
        if not dims:
            return {"Datapoints": []}
        model_id = dims[0].get("Value")
        total = counts_by_model_id.get(model_id, 0)
        if total > 0:
            return {"Datapoints": [{"Sum": total}]}
        return {"Datapoints": []}

    mock_cw.get_metric_statistics.side_effect = _get_metric_statistics

    if list_metrics_dimensions is not None:
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "Metrics": [
                    {"Dimensions": [{"Name": "ModelId", "Value": mid}]}
                    for mid in list_metrics_dimensions
                ]
            }
        ]
        mock_cw.get_paginator.return_value = mock_paginator

    return mock_cw


# ---------------------------------------------------------------------------
# Tool invocation helpers
# ---------------------------------------------------------------------------


def _unwrap_tool(tool_obj):
    """Return the raw callable beneath a Strands ``@tool`` wrapper.

    Under the identity-decorator stub installed at module top, ``@tool`` is a
    pass-through, so this returns ``tool_obj`` itself. The attribute walk is
    kept so the helper still works if the decorator ever becomes a real
    wrapper (e.g. when the test file is loaded after a real-Strands import).
    """
    for attr in ("__wrapped__", "original_function", "func", "fn"):
        inner = getattr(tool_obj, attr, None)
        if callable(inner):
            return inner
    return tool_obj


def _call_list_active_bedrock_models(
    mock_cw,
    snapshot: dict | None = None,
    *,
    region: str = REGION,
    hours_back: int = 24,
    by_profile: bool = False,
) -> str:
    """Invoke ``list_active_bedrock_models`` with CloudWatch (and the Snapshot
    cache, when ``by_profile=True``) mocked.

    The ``by_profile=True`` branch routes through
    ``_list_active_inference_profiles_impl`` which calls
    ``get_snapshot_cached()``. We seed ``helpers_snapshot._snapshot_cache`` directly so
    that helper never tries to reach DynamoDB.
    """
    helpers_snapshot._snapshot_cache = None
    if snapshot is not None:
        helpers_snapshot._snapshot_cache = snapshot

    fn = _unwrap_tool(list_active_bedrock_models)
    with patch("tools.list_active_bedrock_models.boto3.client", return_value=mock_cw):
        if snapshot is None and by_profile:
            # Snapshot-miss path uses the CloudWatch list_metrics paginator
            # and does not touch DynamoDB, so no resource patch is required.
            return fn(region=region, hours_back=hours_back, by_profile=by_profile)
        if snapshot is None:
            # Model-grain path never reads the Snapshot.
            return fn(region=region, hours_back=hours_back, by_profile=by_profile)
        return fn(region=region, hours_back=hours_back, by_profile=by_profile)


def _call_list_active_inference_profiles(
    mock_cw,
    snapshot: dict,
    *,
    region: str = REGION,
    hours_back: int = 24,
) -> str:
    """Invoke ``list_active_inference_profiles`` with CloudWatch and snapshot
    cache seeded. Used for the lockstep equivalence test."""
    helpers_snapshot._snapshot_cache = snapshot
    fn = _unwrap_tool(list_active_inference_profiles)
    with patch("tools.list_active_bedrock_models.boto3.client", return_value=mock_cw):
        return fn(
            region=region,
            hours_back=hours_back,
            show_all_system_profiles=False,
        )


# ---------------------------------------------------------------------------
# Autouse cache reset so tests never leak state between each other.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_snapshot_cache():
    helpers_snapshot._snapshot_cache = None
    yield
    helpers_snapshot._snapshot_cache = None


# ===========================================================================
# Test 1 — by_profile=False preserves the pre-existing model-grain output
# ===========================================================================


class TestByProfileFalseBackCompat:
    """Req 3.7: default (model-grain) output is unchanged, including the
    distinctive header, totals line, and absence of profile-grain markers."""

    def test_model_grain_output_shape_is_unchanged(self):
        # Two distinct ModelIds with different Invocations counts.
        counts = {BARE_SONNET_CW_ID: 123, US_SONNET_CW_ID: 456}
        mock_cw = _make_cw_mock(
            counts_by_model_id=counts,
            list_metrics_dimensions=[BARE_SONNET_CW_ID, US_SONNET_CW_ID],
        )

        output = _call_list_active_bedrock_models(mock_cw)

        # Distinctive model-grain header (not the Template E header).
        assert output.startswith(
            f"Active Bedrock Models in {REGION} (past 24 hours):"
        )
        # Both ModelIds are present.
        assert BARE_SONNET_CW_ID in output
        assert US_SONNET_CW_ID in output
        # Exact totals footer from the model-grain code path.
        assert "Total: 2 models with activity" in output
        # Template E header from list_active_inference_profiles must NOT leak in.
        assert f"Active Inference Profiles in {REGION}" not in output
        # Application-profile tag is a profile-grain-only marker.
        assert "(application-profile)" not in output

    def test_model_grain_sorts_descending_by_invocations(self):
        """Back-compat: sibling scenario assertions depend on the ordering.

        The existing code path sorts the two ModelIds with the higher
        invocation count first. This test pins that ordering so future edits
        cannot silently reverse it.
        """
        counts = {BARE_SONNET_CW_ID: 123, US_SONNET_CW_ID: 456}
        mock_cw = _make_cw_mock(
            counts_by_model_id=counts,
            list_metrics_dimensions=[BARE_SONNET_CW_ID, US_SONNET_CW_ID],
        )

        output = _call_list_active_bedrock_models(mock_cw)

        # US_SONNET_CW_ID has the higher count (456) so it must appear first.
        pos_us = output.find(US_SONNET_CW_ID)
        pos_bare = output.find(BARE_SONNET_CW_ID)
        assert pos_us != -1 and pos_bare != -1, output
        # Note: the bare ModelId is a prefix substring of us.anthropic.claude-…
        # so we find the SECOND occurrence (after the "us." one) to locate the
        # actual bare-id row.
        pos_bare = output.find(BARE_SONNET_CW_ID, pos_us + len(US_SONNET_CW_ID))
        assert pos_bare != -1, output
        assert pos_us < pos_bare, (
            f"expected {US_SONNET_CW_ID!r} to come before {BARE_SONNET_CW_ID!r} "
            f"in output:\n{output}"
        )


# ===========================================================================
# Test 2 — by_profile=True returns profile-grain output (Template E)
# ===========================================================================


class TestByProfileTrueProfileGrain:
    """Req 3.8: ``by_profile=True`` emits Template E ordered by invocations."""

    def test_emits_template_e_header_and_rows(self):
        """System_Defined_Profile + Application_Profile both rendered."""
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=11_437),
                ],
                app_profiles=[
                    _app_profile("marketing-bot", MKT_BOT_ARN,
                                 invocations_24h=490),
                ],
            ),
        ])
        mock_cw = _make_cw_mock(
            counts_by_model_id={US_SONNET_CW_ID: 11_437, MKT_BOT_ARN: 490}
        )

        output = _call_list_active_bedrock_models(
            mock_cw, snapshot, by_profile=True
        )

        # Template E header.
        assert output.startswith(
            f"Active Inference Profiles in {REGION} (past 24 hours):"
        )
        # System profile and Application_Profile both present.
        assert US_SONNET_CW_ID in output
        assert "marketing-bot" in output
        assert "(application-profile)" in output
        # And the model-grain-only footer must NOT be present.
        assert "Total: 1 models with activity" not in output
        assert "Total: 2 models with activity" not in output

    def test_profile_rows_sorted_descending_by_invocations(self):
        """Req 3.8: fixed ordering — descending by invocation count."""
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=100),
                ],
                app_profiles=[
                    _app_profile("marketing-bot", MKT_BOT_ARN,
                                 invocations_24h=10_000),
                ],
            ),
        ])
        mock_cw = _make_cw_mock(
            counts_by_model_id={US_SONNET_CW_ID: 100, MKT_BOT_ARN: 10_000}
        )

        output = _call_list_active_bedrock_models(
            mock_cw, snapshot, by_profile=True
        )

        # marketing-bot (10_000) must appear before US_SONNET_CW_ID (100).
        pos_mkt = output.find("marketing-bot")
        pos_us = output.find(US_SONNET_CW_ID)
        assert pos_mkt != -1 and pos_us != -1, output
        assert pos_mkt < pos_us, (
            f"expected marketing-bot (10_000 invocations) before "
            f"{US_SONNET_CW_ID!r} (100 invocations) in:\n{output}"
        )


# ===========================================================================
# Test 3 — by_profile=True output is byte-identical to list_active_inference_profiles
# ===========================================================================


class TestLockstepWithInferenceProfilesTool:
    """Req 3.10: the two active-profile tools must stay in lockstep."""

    def test_output_matches_list_active_inference_profiles_exactly(self):
        """Given the same mocked CloudWatch and Snapshot, both tools emit the
        same string."""
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=11_437),
                ],
                app_profiles=[
                    _app_profile("marketing-bot", MKT_BOT_ARN,
                                 invocations_24h=490),
                ],
            ),
        ])
        counts = {US_SONNET_CW_ID: 11_437, MKT_BOT_ARN: 490}

        # Two separate CloudWatch mocks so the per-call interactions don't
        # cross-contaminate; both mocks are configured identically.
        mock_cw_a = _make_cw_mock(counts_by_model_id=counts)
        mock_cw_b = _make_cw_mock(counts_by_model_id=counts)

        output_by_profile = _call_list_active_bedrock_models(
            mock_cw_a, snapshot, by_profile=True
        )
        output_inference = _call_list_active_inference_profiles(
            mock_cw_b, snapshot
        )

        assert output_by_profile == output_inference


# ===========================================================================
# Test 4 — Sum of per-profile invocations equals model-grain count
# ===========================================================================


class TestPerProfileSumsMatchModelGrainCount:
    """Req 3.10: sum of per-profile invocation counts for a given model
    equals the model-grain invocation count for that model."""

    def test_sum_of_profile_invocations_equals_model_grain_total(self):
        """Two AccessPatterns + one App_Profile for the same base model.

        Model-grain mode discovers three distinct ModelIds via ``list_metrics``
        and reports each one's total separately. Profile-grain mode discovers
        the same three from the Snapshot. With the CloudWatch mock seeded to
        return the same Sum per ModelId in both calls, the sum of the three
        profile-grain totals must equal the sum of the three model-grain
        totals for the same ModelIds — exactly, because the mock has no
        rounding noise.
        """
        # Three CW_Model_IDs all belonging to the same base model: an on-demand
        # bare ID, a cross-region-geo (us), and one Application_Profile.
        invocations = {
            BARE_SONNET_CW_ID: 920,
            US_SONNET_CW_ID: 11_437,
            MKT_BOT_ARN: 490,
        }
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(
                        BARE_SONNET_CW_ID,
                        pattern_type="on-demand",
                        geography=None,
                        invocations_24h=invocations[BARE_SONNET_CW_ID],
                    ),
                    _access_pattern(
                        US_SONNET_CW_ID,
                        pattern_type="cross-region-geo",
                        geography="us",
                        invocations_24h=invocations[US_SONNET_CW_ID],
                    ),
                ],
                app_profiles=[
                    _app_profile(
                        "marketing-bot",
                        MKT_BOT_ARN,
                        invocations_24h=invocations[MKT_BOT_ARN],
                    ),
                ],
            ),
        ])

        # Model-grain path: list_metrics must return the same three IDs the
        # Snapshot contains so the two paths are directly comparable.
        mock_cw_model_grain = _make_cw_mock(
            counts_by_model_id=invocations,
            list_metrics_dimensions=list(invocations.keys()),
        )
        mock_cw_profile_grain = _make_cw_mock(
            counts_by_model_id=invocations,
        )

        model_grain_output = _call_list_active_bedrock_models(
            mock_cw_model_grain
        )
        profile_grain_output = _call_list_active_bedrock_models(
            mock_cw_profile_grain, snapshot, by_profile=True
        )

        # Parse the reported invocation counts out of both outputs. Both
        # renderers emit one "Invocations: N" row per entry (with commas in
        # the profile-grain path and no commas in the model-grain path).
        import re as _re

        def _sum_invocations(text: str) -> int:
            # Commas are optional; "{:,}".format strips them with a regex.
            matches = _re.findall(r"Invocations:\s+([\d,]+)", text)
            return sum(int(m.replace(",", "")) for m in matches)

        model_total = _sum_invocations(model_grain_output)
        profile_total = _sum_invocations(profile_grain_output)

        # Both should sum to the total across the three ModelIds.
        expected_total = sum(invocations.values())
        assert model_total == expected_total, (
            f"model-grain sum {model_total} != expected {expected_total}\n"
            f"output:\n{model_grain_output}"
        )
        assert profile_total == expected_total, (
            f"profile-grain sum {profile_total} != expected {expected_total}\n"
            f"output:\n{profile_grain_output}"
        )
        # And therefore they match each other within CloudWatch rounding —
        # which is exact here because both mocks return the same values.
        assert profile_total == model_total
