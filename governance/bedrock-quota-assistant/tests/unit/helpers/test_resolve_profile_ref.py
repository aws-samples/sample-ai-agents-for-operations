"""
Unit tests for ``agent.resolve_profile_ref``.

Covers the first-match-wins resolution table described in
``.kiro/specs/per-profile-metrics/design.md`` §"Profile_Ref Resolution
Algorithm" and Requirements 4.1–4.6, 5.2, 5.5:

  Branch 1: Application_Profile ARN regex match                 (Req 4.1)
  Branch 2: ``us.`` / ``eu.`` / ``ap.`` / ``jp.`` / ``global.`` (Req 4.3)
  Branch 3: case-insensitive exact match on Snapshot App_Profile names
            (Req 4.2 single match; Req 4.5 ambiguous)
  Branch 4: foundation-model alias + trailing profile-family keyword
            (e.g. ``"claude sonnet 4.6 global"``)                (Req 4.4)
  Branch 5: bare foundation-model alias fallback                 (bare-model back-compat)
  Branch 6: nothing matched → kind="unresolved"                  (Req 4.6)

Also verifies ``underlying_quota_scope`` population per Req 5.2 and 5.5:
  - System_Defined_Profile        → "cross-region"
  - Application_Profile w/ wraps  → matches ``wraps`` field
  - Application_Profile w/o wraps → None (Req 5.5)
  - Bare foundation model         → "on-demand"

Snapshot=None degradation (Req 1.4): branches 1, 2, 4 (system_profile leg),
5 still resolve; branch 3 is skipped so a friendly name falls through to
branch 6.

Import pattern mirrors ``test_is_active_inference_profile.py`` and
``test_snapshot_cache.py``: third-party modules are inserted into
``sys.modules`` before importing ``agent`` so the import never touches the
real boto3 / strands / bedrock_agentcore stacks. Because ``agent.py`` does
``from models import resolve_model_id, ...``, the ``models`` module is
mocked and ``agent.resolve_model_id`` is a ``MagicMock``; tests that need
branch 4 or branch 5 to resolve a specific alias patch
``resolve_model_id`` via ``unittest.mock.patch.object`` on the
``helpers.profile_resolution`` module.
"""

from unittest.mock import patch

import pytest

from helpers.profile_resolution import resolve_profile_ref


# ---------------------------------------------------------------------------
# Snapshot fixtures
# ---------------------------------------------------------------------------
#
# The Snapshot schema used here is a faithful minimal subset of the
# ``CustomerProfileSnapshot`` defined in
# ``.kiro/specs/customer-profile/design.md``. Only the fields
# ``resolve_profile_ref`` actually reads are populated.
#
# Three models are configured so we can cover every branch with a single
# fixture:
#   - claude-sonnet-4-6 with one App_Profile ``marketing-bot`` wrapping
#     ``cross-region``
#   - nova-pro with one App_Profile ``analytics-bot`` wrapping ``on-demand``
#   - claude-sonnet-4-5 with one App_Profile whose ``wraps`` field is missing
#     — used for Req 5.5 (Underlying_Quota cannot be determined)
#
# For ambiguity tests we build separate Snapshots that pin exactly 2 and 3
# matches for the same case-insensitive name.

MKT_BOT_ARN = (
    "arn:aws:bedrock:us-east-1:123456789012:"
    "application-inference-profile/mkt-bot-abc123"
)
ANALYTICS_BOT_ARN = (
    "arn:aws:bedrock:us-west-2:123456789012:"
    "application-inference-profile/analytics-bot-def456"
)
ORPHAN_ARN = (
    "arn:aws:bedrock:eu-west-1:123456789012:"
    "application-inference-profile/orphan-ghi789"
)


def _base_snapshot() -> dict:
    """Return a snapshot covering the common single-match Application_Profile cases.

    - ``marketing-bot`` wraps cross-region → underlying_quota_scope == "cross-region"
    - ``analytics-bot`` wraps on-demand   → underlying_quota_scope == "on-demand"
    - ``orphan-bot`` is present but has no ``wraps`` field  → underlying_quota_scope == None (Req 5.5)
    """
    return {
        "PK": "customer-profile",
        "SK": "latest",
        "models": [
            {
                "model_id": "anthropic.claude-sonnet-4-6-v1:0",
                "provider": "Anthropic",
                "active_patterns": [],
                "app_profiles": [
                    {
                        "name": "marketing-bot",
                        "arn": MKT_BOT_ARN,
                        "tags": {},
                        "invocations_24h": 490,
                        "wraps": "cross-region",
                    }
                ],
            },
            {
                "model_id": "amazon.nova-pro-v1:0",
                "provider": "Amazon",
                "active_patterns": [],
                "app_profiles": [
                    {
                        "name": "analytics-bot",
                        "arn": ANALYTICS_BOT_ARN,
                        "tags": {},
                        "invocations_24h": 120,
                        "wraps": "on-demand",
                    }
                ],
            },
            {
                "model_id": "anthropic.claude-sonnet-4-5-20250929-v1:0",
                "provider": "Anthropic",
                "active_patterns": [],
                "app_profiles": [
                    {
                        "name": "orphan-bot",
                        "arn": ORPHAN_ARN,
                        "tags": {},
                        "invocations_24h": 0,
                        # No ``wraps`` field — Req 5.5 scenario.
                    }
                ],
            },
        ],
    }


def _ambiguous_snapshot(num_matches: int) -> dict:
    """Build a Snapshot where ``num_matches`` Application_Profiles share a name.

    The name is ``"shared-bot"`` (case-insensitive) across distinct ARNs and
    different parent base models. Used to exercise Req 4.5 with both 2-way
    and 3-way ambiguity.
    """
    assert num_matches in (2, 3), "test helper only configured for 2- or 3-way"
    parents = [
        ("anthropic.claude-sonnet-4-6-v1:0", "us-east-1", "abc"),
        ("amazon.nova-pro-v1:0", "us-west-2", "def"),
        ("anthropic.claude-sonnet-4-5-20250929-v1:0", "eu-west-1", "ghi"),
    ][:num_matches]
    return {
        "PK": "customer-profile",
        "SK": "latest",
        "models": [
            {
                "model_id": base_id,
                "provider": "X",
                "active_patterns": [],
                "app_profiles": [
                    {
                        "name": "shared-bot",
                        "arn": (
                            f"arn:aws:bedrock:{region}:123456789012:"
                            f"application-inference-profile/shared-{suffix}"
                        ),
                        "tags": {},
                        "invocations_24h": 0,
                        "wraps": "cross-region",
                    }
                ],
            }
            for (base_id, region, suffix) in parents
        ],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_resolve_model_id(mapping: dict[str, str | None]):
    """Context manager that makes ``resolve_model_id`` return values per alias.

    Tests exercising branches 4 and 5 need deterministic alias → model-id
    lookups, so we patch ``resolve_model_id`` in the module where
    ``resolve_profile_ref`` actually calls it. Lookups that are not in
    ``mapping`` return ``None`` (the same "not found" signal the real
    ``models.resolve_model_id`` returns).
    """
    import helpers.profile_resolution as _pr

    def side_effect(value: str) -> str | None:
        return mapping.get(value)

    return patch.object(_pr, "resolve_model_id", side_effect=side_effect)


# ---------------------------------------------------------------------------
# Branch 1 — Application_Profile ARN
# ---------------------------------------------------------------------------


class TestBranch1ArnMatch:
    """Resolution via direct Application_Profile ARN match (Req 4.1)."""

    def test_arn_found_in_snapshot_wraps_cross_region(self):
        """Snapshot look-up populates every field including ``underlying_quota_scope``."""
        result = resolve_profile_ref(MKT_BOT_ARN, _base_snapshot())

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == MKT_BOT_ARN
        assert result["application_profile_arn"] == MKT_BOT_ARN
        assert result["application_profile_name"] == "marketing-bot"
        assert result["base_model_id"] == "anthropic.claude-sonnet-4-6-v1:0"
        assert result["underlying_quota_scope"] == "cross-region"
        assert result["candidates"] == []
        assert result["unresolved_ref"] is None

    def test_arn_found_in_snapshot_wraps_on_demand(self):
        """``wraps="on-demand"`` propagates to ``underlying_quota_scope``."""
        result = resolve_profile_ref(ANALYTICS_BOT_ARN, _base_snapshot())

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == ANALYTICS_BOT_ARN
        assert result["application_profile_name"] == "analytics-bot"
        assert result["base_model_id"] == "amazon.nova-pro-v1:0"
        assert result["underlying_quota_scope"] == "on-demand"

    def test_arn_found_but_wraps_missing_yields_none_scope(self):
        """Req 5.5 — Application_Profile present but ``wraps`` absent → scope is None."""
        result = resolve_profile_ref(ORPHAN_ARN, _base_snapshot())

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == ORPHAN_ARN
        assert result["application_profile_name"] == "orphan-bot"
        assert result["base_model_id"] == (
            "anthropic.claude-sonnet-4-5-20250929-v1:0"
        )
        # wraps field missing → scope must stay None, never default to a guess.
        assert result["underlying_quota_scope"] is None

    def test_arn_not_in_snapshot_still_resolves(self):
        """An ARN that matches the regex but is absent from Snapshot still routes
        through branch 1 with ``cw_model_id=arn``; enrichment fields stay None.
        """
        unknown_arn = (
            "arn:aws:bedrock:us-east-1:123456789012:"
            "application-inference-profile/not-in-snapshot-xyz"
        )
        result = resolve_profile_ref(unknown_arn, _base_snapshot())

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == unknown_arn
        assert result["application_profile_arn"] == unknown_arn
        assert result["application_profile_name"] is None
        assert result["base_model_id"] is None
        assert result["underlying_quota_scope"] is None


# ---------------------------------------------------------------------------
# Branch 2 — System_Defined_Profile prefixes
# ---------------------------------------------------------------------------

SYSTEM_PROFILE_CASES = [
    ("us.",     "us.anthropic.claude-sonnet-4-6-v1:0",     "anthropic.claude-sonnet-4-6-v1:0"),
    ("eu.",     "eu.anthropic.claude-sonnet-4-6-v1:0",     "anthropic.claude-sonnet-4-6-v1:0"),
    ("ap.",     "ap.anthropic.claude-sonnet-4-6-v1:0",     "anthropic.claude-sonnet-4-6-v1:0"),
    ("jp.",     "jp.anthropic.claude-sonnet-4-6-v1:0",     "anthropic.claude-sonnet-4-6-v1:0"),
    ("global.", "global.anthropic.claude-sonnet-4-6-v1:0", "anthropic.claude-sonnet-4-6-v1:0"),
]


class TestBranch2SystemProfilePrefix:
    """Resolution via System_Defined_Profile prefix (Req 4.3)."""

    @pytest.mark.parametrize(
        "prefix, ref, expected_base",
        SYSTEM_PROFILE_CASES,
        ids=[c[0].rstrip(".") for c in SYSTEM_PROFILE_CASES],
    )
    def test_prefix_resolves_with_snapshot(self, prefix, ref, expected_base):
        """Every supported prefix resolves to kind=system_profile, cross-region scope."""
        result = resolve_profile_ref(ref, _base_snapshot())

        assert result["kind"] == "system_profile", (
            f"prefix {prefix!r} should route through branch 2"
        )
        assert result["cw_model_id"] == ref
        assert result["base_model_id"] == expected_base
        # System_Defined_Profiles always consume the cross-region quota.
        assert result["underlying_quota_scope"] == "cross-region"
        assert result["application_profile_arn"] is None
        assert result["application_profile_name"] is None

    @pytest.mark.parametrize(
        "prefix, ref, expected_base",
        SYSTEM_PROFILE_CASES,
        ids=[c[0].rstrip(".") for c in SYSTEM_PROFILE_CASES],
    )
    def test_prefix_resolves_without_snapshot(self, prefix, ref, expected_base):
        """Prefix is self-describing — resolves even when Snapshot is None (Req 1.4)."""
        result = resolve_profile_ref(ref, None)

        assert result["kind"] == "system_profile"
        assert result["cw_model_id"] == ref
        assert result["base_model_id"] == expected_base
        assert result["underlying_quota_scope"] == "cross-region"


# ---------------------------------------------------------------------------
# Branch 3 — Case-insensitive Application_Profile name match
# ---------------------------------------------------------------------------


class TestBranch3NameMatch:
    """Resolution via case-insensitive exact name match (Req 4.2)."""

    @pytest.mark.parametrize(
        "ref",
        ["marketing-bot", "Marketing-Bot", "MARKETING-BOT", "mArKeTiNg-BoT"],
        ids=["exact-lower", "title-case", "upper", "mixed-case"],
    )
    def test_single_name_match_case_insensitive(self, ref):
        """Any casing of a unique Application_Profile name resolves to that profile."""
        # Prevent branch 5 (bare alias fallback) from short-circuiting this
        # test by ensuring ``resolve_model_id`` does not claim to know the
        # name — the real alias resolver returns None for arbitrary names.
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(ref, _base_snapshot())

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == MKT_BOT_ARN
        assert result["application_profile_arn"] == MKT_BOT_ARN
        assert result["application_profile_name"] == "marketing-bot"
        assert result["base_model_id"] == "anthropic.claude-sonnet-4-6-v1:0"
        assert result["underlying_quota_scope"] == "cross-region"

    def test_single_name_match_on_demand_wraps(self):
        """``analytics-bot`` wraps on-demand — scope propagates from Snapshot."""
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref("analytics-bot", _base_snapshot())

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == ANALYTICS_BOT_ARN
        assert result["underlying_quota_scope"] == "on-demand"

    def test_single_name_match_wraps_missing(self):
        """Req 5.5 — name matches but ``wraps`` is absent → scope stays None."""
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref("orphan-bot", _base_snapshot())

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == ORPHAN_ARN
        assert result["underlying_quota_scope"] is None


class TestBranch3Ambiguity:
    """Req 4.5 — two or more candidates → kind="ambiguous", no guess."""

    def test_two_way_ambiguity(self):
        """Two Application_Profiles share a name → ambiguous with 2 candidates."""
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(
                "shared-bot", _ambiguous_snapshot(num_matches=2)
            )

        assert result["kind"] == "ambiguous"
        assert result["cw_model_id"] is None
        assert result["underlying_quota_scope"] is None
        assert len(result["candidates"]) == 2
        # Each candidate label must contain a distinguishing qualifier so the
        # user can pick one — at minimum the region or parent base-model ID.
        for label in result["candidates"]:
            assert "shared-bot" in label
            distinguishes = (
                "us-east-1" in label
                or "us-west-2" in label
                or "claude-sonnet-4-6" in label
                or "nova-pro" in label
            )
            assert distinguishes, (
                f"candidate label {label!r} must carry a region or base-model "
                f"qualifier so the user can disambiguate"
            )

    def test_three_way_ambiguity(self):
        """Three Application_Profiles share a name → ambiguous with 3 candidates."""
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(
                "shared-bot", _ambiguous_snapshot(num_matches=3)
            )

        assert result["kind"] == "ambiguous"
        assert len(result["candidates"]) == 3
        # All three regions should be represented across the candidate labels
        # so the user sees every distinguishing qualifier.
        all_labels = " | ".join(result["candidates"])
        for region in ("us-east-1", "us-west-2", "eu-west-1"):
            assert region in all_labels, (
                f"expected region {region!r} to appear in at least one candidate"
            )

    def test_ambiguity_case_insensitive(self):
        """Case-insensitive matching still triggers ambiguity with 2+ matches."""
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(
                "SHARED-BOT", _ambiguous_snapshot(num_matches=2)
            )
        assert result["kind"] == "ambiguous"
        assert len(result["candidates"]) == 2


# ---------------------------------------------------------------------------
# Branch 4 — alias + profile-family keyword
# ---------------------------------------------------------------------------


class TestBranch4AliasPlusKeyword:
    """Resolution via ``"<alias> <family>"`` composition (Req 4.4)."""

    def test_claude_sonnet_46_global(self):
        """``"claude sonnet 4.6 global"`` → ``global.<resolved base id>``."""
        base = "anthropic.claude-sonnet-4-6-v1:0"
        with _patch_resolve_model_id({"claude sonnet 4.6": base}):
            result = resolve_profile_ref(
                "claude sonnet 4.6 global", _base_snapshot()
            )

        assert result["kind"] == "system_profile"
        assert result["cw_model_id"] == f"global.{base}"
        assert result["base_model_id"] == base
        assert result["underlying_quota_scope"] == "cross-region"

    def test_nova_pro_eu(self):
        """``"nova pro eu"`` → ``eu.<resolved base id>``."""
        base = "amazon.nova-pro-v1:0"
        with _patch_resolve_model_id({"nova pro": base}):
            result = resolve_profile_ref("nova pro eu", _base_snapshot())

        assert result["kind"] == "system_profile"
        assert result["cw_model_id"] == f"eu.{base}"
        assert result["base_model_id"] == base
        assert result["underlying_quota_scope"] == "cross-region"

    @pytest.mark.parametrize(
        "keyword", ["us", "eu", "ap", "jp", "global"],
    )
    def test_every_family_keyword(self, keyword):
        """Every profile-family keyword participates in branch 4 composition."""
        base = "anthropic.claude-sonnet-4-6-v1:0"
        with _patch_resolve_model_id({"claude sonnet 4.6": base}):
            result = resolve_profile_ref(
                f"claude sonnet 4.6 {keyword}", None  # snapshot irrelevant here
            )

        assert result["kind"] == "system_profile"
        assert result["cw_model_id"] == f"{keyword}.{base}"

    def test_keyword_case_insensitive(self):
        """Trailing keyword matches regardless of casing (``GLOBAL``, ``Global``, ...)."""
        base = "anthropic.claude-sonnet-4-6-v1:0"
        with _patch_resolve_model_id({"claude sonnet 4.6": base}):
            result = resolve_profile_ref(
                "claude sonnet 4.6 GLOBAL", None
            )
        assert result["kind"] == "system_profile"
        assert result["cw_model_id"] == f"global.{base}"

    def test_alias_unresolvable_falls_through_to_branch_5(self):
        """If the head doesn't resolve, branch 4 falls through.

        Branch 5 then retries with the full ref. If branch 5 also fails, the
        resolver returns unresolved.
        """
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(
                "unknown-alias global", _base_snapshot()
            )

        assert result["kind"] == "unresolved"
        assert result["unresolved_ref"] == "unknown-alias global"


# ---------------------------------------------------------------------------
# Branch 5 — bare-alias fallback
# ---------------------------------------------------------------------------


class TestBranch5BareAlias:
    """Resolution via bare foundation-model alias fallback."""

    def test_bare_alias_resolves_to_model(self):
        """``"claude sonnet 4.6"`` → kind=model, cw_model_id=resolved base id."""
        base = "anthropic.claude-sonnet-4-6-v1:0"
        with _patch_resolve_model_id({"claude sonnet 4.6": base}):
            result = resolve_profile_ref(
                "claude sonnet 4.6", _base_snapshot()
            )

        assert result["kind"] == "model"
        assert result["cw_model_id"] == base
        assert result["base_model_id"] == base
        assert result["underlying_quota_scope"] == "on-demand"
        assert result["application_profile_arn"] is None


# ---------------------------------------------------------------------------
# Branch 6 — unresolved
# ---------------------------------------------------------------------------


class TestBranch6Unresolved:
    """Nothing matched → kind="unresolved" with the original ref recorded."""

    def test_unknown_ref_with_snapshot(self):
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(
                "totally-made-up-thing-xyz", _base_snapshot()
            )
        assert result["kind"] == "unresolved"
        assert result["cw_model_id"] is None
        assert result["unresolved_ref"] == "totally-made-up-thing-xyz"
        assert result["underlying_quota_scope"] is None

    def test_unknown_ref_without_snapshot(self):
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref("not-a-profile", None)
        assert result["kind"] == "unresolved"
        assert result["unresolved_ref"] == "not-a-profile"


# ---------------------------------------------------------------------------
# Fall-through corner cases
# ---------------------------------------------------------------------------


class TestFallThroughCorners:
    """Malformed ARNs and unknown prefixes must not be mistaken for branch 1/2."""

    def test_malformed_arn_does_not_route_through_branch_1(self):
        """An ARN-ish string that fails the regex falls through to later branches.

        ``arn:aws:bedrock:us-east-1::foo/bar`` lacks the
        ``application-inference-profile/`` segment, so the branch-1 regex
        rejects it. The ref then falls through; since branch 5 cannot
        resolve it either, we expect kind="unresolved".
        """
        malformed = "arn:aws:bedrock:us-east-1::foo/bar"
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(malformed, _base_snapshot())

        assert result["kind"] != "application_profile", (
            "malformed ARN must not be silently treated as an Application_Profile"
        )
        assert result["kind"] == "unresolved"
        assert result["unresolved_ref"] == malformed

    def test_unknown_prefix_falls_through(self):
        """``"foo.anthropic.claude-sonnet-4-6-v1:0"`` has an unknown prefix.

        ``foo.`` is not in ``{us., eu., ap., jp., global.}`` so branch 2
        rejects it, and no alias matches, so the resolver returns unresolved.
        """
        ref = "foo.anthropic.claude-sonnet-4-6-v1:0"
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(ref, _base_snapshot())

        assert result["kind"] == "unresolved"
        assert result["unresolved_ref"] == ref


# ---------------------------------------------------------------------------
# Snapshot=None degradation
# ---------------------------------------------------------------------------


class TestSnapshotNoneDegradation:
    """Req 1.4 + Req 4.6 — graceful degradation when Snapshot is unavailable.

    - Branch 1 (ARN): still resolves to ``kind="application_profile"`` but
      enrichment fields (name, base_model_id, underlying_quota_scope) are all
      None because there is no Snapshot to look them up in.
    - Branch 2 (prefix): still resolves (already covered by
      ``TestBranch2SystemProfilePrefix::test_prefix_resolves_without_snapshot``).
    - Branch 3 (name): skipped entirely — friendly-name lookups fall through.
    """

    def test_arn_resolves_but_enrichment_fields_are_none(self):
        result = resolve_profile_ref(MKT_BOT_ARN, None)

        assert result["kind"] == "application_profile"
        assert result["cw_model_id"] == MKT_BOT_ARN
        assert result["application_profile_arn"] == MKT_BOT_ARN
        # Without Snapshot we cannot know these:
        assert result["application_profile_name"] is None
        assert result["base_model_id"] is None
        assert result["underlying_quota_scope"] is None

    def test_prefix_resolves_without_snapshot(self):
        """Sanity check — branch 2 is fully self-describing."""
        result = resolve_profile_ref(
            "global.anthropic.claude-sonnet-4-6-v1:0", None
        )
        assert result["kind"] == "system_profile"
        assert (
            result["cw_model_id"] == "global.anthropic.claude-sonnet-4-6-v1:0"
        )
        assert result["underlying_quota_scope"] == "cross-region"

    def test_friendly_name_without_snapshot_is_unresolved(self):
        """Friendly Application_Profile names cannot be resolved without Snapshot."""
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref("marketing-bot", None)

        assert result["kind"] == "unresolved"
        assert result["unresolved_ref"] == "marketing-bot"
        assert result["cw_model_id"] is None
        assert result["underlying_quota_scope"] is None


# ---------------------------------------------------------------------------
# Underlying_Quota scope table (Req 5.2, 5.5)
# ---------------------------------------------------------------------------


class TestUnderlyingQuotaScope:
    """Consolidated table for ``underlying_quota_scope`` values (Req 5.2, 5.5)."""

    def test_app_profile_wraps_cross_region(self):
        result = resolve_profile_ref(MKT_BOT_ARN, _base_snapshot())
        assert result["underlying_quota_scope"] == "cross-region"

    def test_app_profile_wraps_on_demand(self):
        result = resolve_profile_ref(ANALYTICS_BOT_ARN, _base_snapshot())
        assert result["underlying_quota_scope"] == "on-demand"

    def test_app_profile_missing_wraps_yields_none(self):
        """Req 5.5 — the wrapping target is present but its ``wraps`` field is absent."""
        result = resolve_profile_ref(ORPHAN_ARN, _base_snapshot())
        assert result["underlying_quota_scope"] is None

    def test_system_profile_is_cross_region(self):
        result = resolve_profile_ref(
            "us.anthropic.claude-sonnet-4-6-v1:0", _base_snapshot()
        )
        assert result["underlying_quota_scope"] == "cross-region"

    def test_bare_model_is_on_demand(self):
        base = "anthropic.claude-sonnet-4-6-v1:0"
        with _patch_resolve_model_id({"claude sonnet 4.6": base}):
            result = resolve_profile_ref("claude sonnet 4.6", None)
        assert result["underlying_quota_scope"] == "on-demand"

    def test_ambiguous_has_none_scope(self):
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref(
                "shared-bot", _ambiguous_snapshot(num_matches=2)
            )
        assert result["underlying_quota_scope"] is None

    def test_unresolved_has_none_scope(self):
        with _patch_resolve_model_id({}):
            result = resolve_profile_ref("nope", None)
        assert result["underlying_quota_scope"] is None
