"""
Unit tests for the ``list_active_inference_profiles`` tool in ``src/agent.py``.

Covers the tool's end-to-end behaviour described in
``.kiro/specs/per-profile-metrics/design.md`` §"Component 1" and Templates E
and F, pinning Requirements 3.1–3.6 and 3.9:

1. Default output excludes sub-threshold System_Defined_Profiles (invocations
   below ``MIN_INVOCATIONS_FOR_ACTIVE``).                              (Req 3.2)
2. Application_Profiles are included unconditionally, and below-threshold
   entries are labelled ``(no recent usage)``.                         (Req 3.3)
3. ``show_all_system_profiles=True`` adds sub-threshold System_Defined_Profiles
   with the ``(inactive — available but unused)`` label.                (Req 3.5)
4. Ranking is descending by invocation count; zero-invocation Application_Profiles
   sort alphabetically last.                                           (Req 3.1)
5. Snapshot-miss fallback uses the live ``list_metrics`` discovery path.
                                                                       (Req 3.9)
6. Empty inventory (Snapshot present but with zero profiles) returns the
   "No Bedrock inference profiles found" message.                      (Req 3.1)
7. Application_Profile section headers include the ``(application-profile)``
   tag.                                                                (Req 3.1)
8. Template E vs Template F header shape differs between default and
   ``show_all_system_profiles=True``.                                   (Req 3.5)
9. Footer reports correct active / inactive counts for both templates.  (Req 3.1, 3.5)

Import pattern mirrors ``test_resolve_profile_ref.py`` and
``test_response_composer.py``: third-party modules are inserted into
``sys.modules`` before importing ``agent`` so the import never touches the
real boto3 / strands / bedrock_agentcore stacks. CloudWatch and the Snapshot
cache are mocked per-test.
"""

from unittest.mock import MagicMock, patch

import pytest


import importlib

import helpers.snapshot as helpers_snapshot
from helpers.profile_resolution import MIN_INVOCATIONS_FOR_ACTIVE

_mod = importlib.import_module("tools.list_active_inference_profiles")
list_active_inference_profiles = _mod.list_active_inference_profiles


# ---------------------------------------------------------------------------
# Constants used across fixtures
# ---------------------------------------------------------------------------

REGION = "us-east-1"

US_SONNET_CW_ID = "us.anthropic.claude-sonnet-4-6-v1:0"
GLOBAL_SONNET_CW_ID = "global.anthropic.claude-sonnet-4-6-v1:0"
EU_SONNET_CW_ID = "eu.anthropic.claude-sonnet-4-6-v1:0"
US_NOVA_CW_ID = "us.amazon.nova-pro-v1:0"

MKT_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/mkt-bot-abc123"
)
RESEARCH_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/res-asst-def456"
)
ZZZ_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/zzz-bot-xyz789"
)
AAA_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/aaa-bot-jkl012"
)


# ---------------------------------------------------------------------------
# Fixture helpers — Snapshot shape matches customer-profile/design.md
# ---------------------------------------------------------------------------


def _access_pattern(
    cw_model_id: str,
    pattern_type: str = "cross-region-geo",
    geography: str | None = "us",
    invocations_24h: int = 100,
) -> dict:
    """Build a minimal AccessPattern entry for Snapshot fixtures."""
    return {
        "pattern_type": pattern_type,
        "cw_model_id": cw_model_id,
        "regions": [REGION],
        "geography": geography,
        "quota_limits": {"rpm_limit": 100, "tpm_limit": 100_000},
        "usage_summary": {"invocations_24h": invocations_24h},
    }


def _app_profile(name: str, arn: str, invocations_24h: int = 0) -> dict:
    """Build a minimal AppProfile entry for Snapshot fixtures."""
    return {
        "name": name,
        "arn": arn,
        "tags": {},
        "invocations_24h": invocations_24h,
        "wraps": "cross-region",
    }


def _snapshot(models: list[dict]) -> dict:
    """Wrap a list of ModelEntry dicts into a Snapshot."""
    return {
        "PK": "customer-profile",
        "SK": "latest",
        "models": models,
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


# ---------------------------------------------------------------------------
# CloudWatch mock factories
# ---------------------------------------------------------------------------


def _make_cw_mock(
    counts_by_model_id: dict[str, int],
    list_metrics_dimensions: list[str] | None = None,
):
    """Build a ``MagicMock`` cloudwatch client.

    ``counts_by_model_id`` drives ``get_metric_statistics``: a model id with a
    positive count returns a single Datapoint with that ``Sum`` value; zero or
    unknown returns an empty ``Datapoints`` list (which the tool totals to 0).

    When ``list_metrics_dimensions`` is provided, the ``list_metrics``
    paginator yields one page whose ``Metrics`` list has one entry per supplied
    model id with a ``ModelId`` dimension. This is the snapshot-miss discovery
    path.
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


def _call_tool(
    mock_cw,
    snapshot: dict | None,
    *,
    region: str = REGION,
    hours_back: int = 24,
    show_all_system_profiles: bool = False,
) -> str:
    """Invoke the tool with ``boto3.client`` and the Snapshot cache both patched.

    Resets ``helpers_snapshot._snapshot_cache`` to ``None`` so ``get_snapshot_cached()``
    does not leak state across tests, then patches the DynamoDB path so the
    first call returns ``snapshot`` (or no-item when ``snapshot is None``).

    The Strands ``@tool`` decorator on ``list_active_inference_profiles``
    replaces the function with a ``DecoratedFunctionTool`` whose ``.invoke(...)``
    is not a plain callable. The underlying function remains accessible via the
    decorator's ``__wrapped__`` / ``original_function`` or by direct attribute
    lookup; here we rely on ``get_snapshot_cached`` returning the right object
    and call the tool via its ``__wrapped__`` if present, else fall back to the
    module-level name (which Strands tool decorators typically preserve).
    """
    # Reset the module-level snapshot cache so each test gets a fresh slot.
    helpers_snapshot._snapshot_cache = None

    # The cleanest way to pin the Snapshot without reaching into DynamoDB is
    # to set the cache slot directly. get_snapshot_cached() short-circuits
    # when the slot is non-None. For the "snapshot=None" path we leave the
    # cache empty and patch boto3.resource to return no Item.
    if snapshot is not None:
        helpers_snapshot._snapshot_cache = snapshot

        # Still patch boto3.client("cloudwatch") so the tool picks up our mock.
        with patch("tools.list_active_inference_profiles.boto3.client", return_value=mock_cw):
            return _invoke_tool(region, hours_back, show_all_system_profiles)
    else:
        # Snapshot path must return None. Patch DynamoDB to return no Item AND
        # patch CloudWatch.
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_resource = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch("tools.list_active_inference_profiles.boto3.client", return_value=mock_cw):
            with patch("helpers.snapshot.boto3.resource", return_value=mock_resource):
                return _invoke_tool(region, hours_back, show_all_system_profiles)


def _invoke_tool(region: str, hours_back: int, show_all_system_profiles: bool) -> str:
    """Call the decorated tool, unwrapping Strands' ``@tool`` wrapper if needed."""
    tool_obj = list_active_inference_profiles
    # Strands' ``@tool`` decorator may expose the raw callable under common
    # attribute names; prefer the raw callable when present.
    for attr in ("__wrapped__", "original_function", "func", "fn"):
        inner = getattr(tool_obj, attr, None)
        if callable(inner):
            return inner(
                region=region,
                hours_back=hours_back,
                show_all_system_profiles=show_all_system_profiles,
            )
    # Fall back to calling the tool object directly — in the MagicMock-stubbed
    # ``strands`` environment this is a plain function.
    return tool_obj(
        region=region,
        hours_back=hours_back,
        show_all_system_profiles=show_all_system_profiles,
    )


# ---------------------------------------------------------------------------
# Autouse reset so one test's cache does not leak into the next.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_snapshot_cache():
    helpers_snapshot._snapshot_cache = None
    yield
    helpers_snapshot._snapshot_cache = None


# ===========================================================================
# Test 1 — default behaviour excludes sub-threshold System_Defined_Profiles
# ===========================================================================


class TestDefaultExcludesSubThresholdSystemProfiles:
    """Req 3.2: System_Defined_Profile below MIN_INVOCATIONS_FOR_ACTIVE is hidden by default."""

    def test_system_profile_with_four_invocations_is_excluded(self):
        """A System_Defined_Profile with 4 invocations (< threshold of 5) is not rendered."""
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=4),
                ],
            ),
        ])
        # Seed CloudWatch with the same 4 invocations so the tool's re-fetch
        # agrees with the Snapshot.
        mock_cw = _make_cw_mock({US_SONNET_CW_ID: 4})

        output = _call_tool(mock_cw, snapshot)

        # The sub-threshold System_Defined_Profile is filtered out.
        assert US_SONNET_CW_ID not in output
        # And since it was the only profile, the tool returns the empty-state
        # message.
        assert "No Bedrock inference profiles found" in output

    def test_system_profile_at_threshold_is_included(self):
        """Exactly MIN_INVOCATIONS_FOR_ACTIVE invocations qualifies."""
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(
                        US_SONNET_CW_ID,
                        invocations_24h=MIN_INVOCATIONS_FOR_ACTIVE,
                    ),
                ],
            ),
        ])
        mock_cw = _make_cw_mock(
            {US_SONNET_CW_ID: MIN_INVOCATIONS_FOR_ACTIVE}
        )

        output = _call_tool(mock_cw, snapshot)

        assert US_SONNET_CW_ID in output


# ===========================================================================
# Test 2 — Application_Profiles always included; "(no recent usage)" label
# ===========================================================================


class TestApplicationProfilesAlwaysIncluded:
    """Req 3.3: App_Profiles are included unconditionally; label when below threshold."""

    def test_zero_invocation_app_profile_is_included_with_label(self):
        snapshot = _snapshot([
            _model_entry(
                app_profiles=[
                    _app_profile("research-assistant", RESEARCH_BOT_ARN,
                                 invocations_24h=0),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({RESEARCH_BOT_ARN: 0})

        output = _call_tool(mock_cw, snapshot)

        assert "research-assistant" in output
        assert "(no recent usage)" in output
        # Application_Profiles are tagged regardless of usage state.
        assert "(application-profile)" in output

    def test_app_profile_above_threshold_omits_no_usage_label(self):
        snapshot = _snapshot([
            _model_entry(
                app_profiles=[
                    _app_profile("marketing-bot", MKT_BOT_ARN,
                                 invocations_24h=490),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({MKT_BOT_ARN: 490})

        output = _call_tool(mock_cw, snapshot)

        assert "marketing-bot" in output
        assert "(no recent usage)" not in output


# ===========================================================================
# Test 3 — show_all_system_profiles=True adds inactive system profiles
# ===========================================================================


class TestShowAllSystemProfiles:
    """Req 3.5: show_all_system_profiles=True surfaces sub-threshold System_Defined_Profiles."""

    def test_inactive_system_profile_visible_only_when_show_all_true(self):
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(GLOBAL_SONNET_CW_ID,
                                    pattern_type="cross-region-global",
                                    geography=None,
                                    invocations_24h=0),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({GLOBAL_SONNET_CW_ID: 0})

        # Default: the sub-threshold System_Defined_Profile is hidden.
        default_output = _call_tool(mock_cw, snapshot)
        assert GLOBAL_SONNET_CW_ID not in default_output

        # With show_all_system_profiles=True: the profile appears with the
        # "(inactive — available but unused)" label.
        all_output = _call_tool(mock_cw, snapshot, show_all_system_profiles=True)
        assert GLOBAL_SONNET_CW_ID in all_output
        assert "(inactive — available but unused)" in all_output


# ===========================================================================
# Test 4 — Ranking is descending by invocation count
# ===========================================================================


class TestRankingDescending:
    """Req 3.1: active profiles are ranked by invocations descending."""

    def test_three_active_system_profiles_sort_by_invocations_desc(self):
        # A=100, B=1000, C=500 — expected order B, C, A.
        snapshot = _snapshot([
            _model_entry(
                model_id="anthropic.claude-sonnet-4-6-v1:0",
                display_name="Claude Sonnet 4.6",
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=100),
                    _access_pattern(EU_SONNET_CW_ID,
                                    geography="eu",
                                    invocations_24h=1_000),
                    _access_pattern(GLOBAL_SONNET_CW_ID,
                                    pattern_type="cross-region-global",
                                    geography=None,
                                    invocations_24h=500),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({
            US_SONNET_CW_ID: 100,
            EU_SONNET_CW_ID: 1_000,
            GLOBAL_SONNET_CW_ID: 500,
        })

        output = _call_tool(mock_cw, snapshot)

        # Check the ordinal position of each profile ID in the output string.
        pos_b = output.find(EU_SONNET_CW_ID)
        pos_c = output.find(GLOBAL_SONNET_CW_ID)
        pos_a = output.find(US_SONNET_CW_ID)
        assert pos_b != -1 and pos_c != -1 and pos_a != -1, output
        assert pos_b < pos_c < pos_a, (
            f"expected EU < GLOBAL < US in output, got positions "
            f"B={pos_b} C={pos_c} A={pos_a}\noutput:\n{output}"
        )


# ===========================================================================
# Test 5 — Zero-invocation App_Profiles sort alphabetically last
# ===========================================================================


class TestZeroInvocationAppProfilesAlphabetical:
    """Req 3.1: zero-invocation App_Profiles sort alphabetically after active profiles."""

    def test_zero_app_profiles_alpha_after_active_system_profile(self):
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=1_000),
                ],
                app_profiles=[
                    _app_profile("zzz-bot", ZZZ_BOT_ARN, invocations_24h=0),
                    _app_profile("aaa-bot", AAA_BOT_ARN, invocations_24h=0),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({
            US_SONNET_CW_ID: 1_000,
            ZZZ_BOT_ARN: 0,
            AAA_BOT_ARN: 0,
        })

        output = _call_tool(mock_cw, snapshot)

        pos_system = output.find(US_SONNET_CW_ID)
        pos_aaa = output.find("aaa-bot")
        pos_zzz = output.find("zzz-bot")
        assert pos_system != -1 and pos_aaa != -1 and pos_zzz != -1, output
        assert pos_system < pos_aaa < pos_zzz, (
            f"expected system < aaa-bot < zzz-bot in output, got "
            f"sys={pos_system} aaa={pos_aaa} zzz={pos_zzz}\noutput:\n{output}"
        )


# ===========================================================================
# Test 6 — Snapshot-miss fallback uses list_metrics discovery
# ===========================================================================


class TestSnapshotMissFallback:
    """Req 3.9: fallback to live ``list_metrics`` discovery when Snapshot is unavailable."""

    def test_fallback_discovers_profiles_from_list_metrics(self):
        """When the Snapshot is missing, discover profiles via the paginator."""
        mock_cw = _make_cw_mock(
            counts_by_model_id={
                US_SONNET_CW_ID: 12_847,
                MKT_BOT_ARN: 490,
            },
            list_metrics_dimensions=[US_SONNET_CW_ID, MKT_BOT_ARN],
        )

        output = _call_tool(mock_cw, snapshot=None)

        # Both profiles should be in the output.
        assert US_SONNET_CW_ID in output
        # Application_Profile's ARN is used as display when no Snapshot name.
        assert MKT_BOT_ARN in output
        # And it should be tagged as an application-profile.
        assert "(application-profile)" in output
        # The paginator was consulted (snapshot-miss path).
        mock_cw.get_paginator.assert_called_once_with("list_metrics")


# ===========================================================================
# Test 7 — Empty inventory (Snapshot present but no profiles)
# ===========================================================================


class TestEmptyInventory:
    """Req 3.1: empty inventory returns the 'No Bedrock inference profiles found' message."""

    def test_empty_models_returns_empty_state_message(self):
        snapshot = _snapshot(models=[])
        mock_cw = _make_cw_mock({})

        output = _call_tool(mock_cw, snapshot)

        assert (
            "No Bedrock inference profiles found in us-east-1 in the past 24 hours."
            in output
        )


# ===========================================================================
# Test 8 — Application_Profile section header includes (application-profile)
# ===========================================================================


class TestApplicationProfileTag:
    """Req 3.1: App_Profile sections carry the ``(application-profile)`` tag."""

    def test_app_profile_section_has_application_profile_tag(self):
        snapshot = _snapshot([
            _model_entry(
                app_profiles=[
                    _app_profile("marketing-bot", MKT_BOT_ARN,
                                 invocations_24h=490),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({MKT_BOT_ARN: 490})

        output = _call_tool(mock_cw, snapshot)

        assert "(application-profile)" in output
        assert "marketing-bot" in output


# ===========================================================================
# Test 9 — Header differs between Template E (default) and Template F (show_all)
# ===========================================================================


class TestTemplateHeaderShape:
    """Req 3.5: Template E vs Template F headers differ in their trailing text."""

    def _tiny_snapshot_with_one_active_system_profile(self):
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=100),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({US_SONNET_CW_ID: 100})
        return snapshot, mock_cw

    def test_default_header_is_template_e(self):
        snapshot, mock_cw = self._tiny_snapshot_with_one_active_system_profile()
        output = _call_tool(mock_cw, snapshot)

        assert "Active Inference Profiles in us-east-1 (past 24 hours):" in output
        # The "showing all system profiles" suffix is the Template F marker;
        # it must NOT be present in the default.
        assert "showing all system profiles" not in output

    def test_show_all_header_is_template_f(self):
        snapshot, mock_cw = self._tiny_snapshot_with_one_active_system_profile()
        output = _call_tool(mock_cw, snapshot, show_all_system_profiles=True)

        assert (
            "Active Inference Profiles in us-east-1 (past 24 hours) — "
            "showing all system profiles:"
            in output
        )


# ===========================================================================
# Test 10 — Footer reports correct counts for both templates
# ===========================================================================


class TestFooterCounts:
    """Req 3.1, 3.5: footer rows summarise active / inactive counts correctly."""

    def test_default_footer_contains_profile_and_active_counts(self):
        # 1 active system profile + 1 zero-invocation app profile.
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=100),
                ],
                app_profiles=[
                    _app_profile("quiet-bot", MKT_BOT_ARN, invocations_24h=0),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({US_SONNET_CW_ID: 100, MKT_BOT_ARN: 0})

        output = _call_tool(mock_cw, snapshot)

        # Two profiles total.
        assert "2 profiles" in output
        # One of them has recent activity (invocations > 0).
        assert "1 with recent activity" in output
        # The zero-invocation app profile is called out in the footer as well.
        assert "no recent usage" in output

    def test_show_all_footer_contains_active_and_inactive_system_counts(self):
        # 1 active system profile + 1 inactive system profile.
        snapshot = _snapshot([
            _model_entry(
                access_patterns=[
                    _access_pattern(US_SONNET_CW_ID, invocations_24h=100),
                    _access_pattern(
                        GLOBAL_SONNET_CW_ID,
                        pattern_type="cross-region-global",
                        geography=None,
                        invocations_24h=0,
                    ),
                ],
            ),
        ])
        mock_cw = _make_cw_mock({US_SONNET_CW_ID: 100, GLOBAL_SONNET_CW_ID: 0})

        output = _call_tool(mock_cw, snapshot, show_all_system_profiles=True)

        assert "2 profiles" in output
        assert "1 active" in output
        assert "1 inactive system profile" in output
