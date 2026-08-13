# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Unit tests for ``_format_for_llm`` from ``src/helpers/snapshot.py``.

Covers the pure function that transforms a structured snapshot dict into
model-optimized markdown for LLM consumption.
"""


from helpers.snapshot import _format_for_llm


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(models=None, assembled_at="2026-06-19T12:00:00Z", regions=None):
    """Build a minimal snapshot dict."""
    return {
        "models": models,
        "assembled_at": assembled_at,
        "regions_scanned": regions or ["us-east-1", "us-west-2"],
    }


def _make_model(
    display_name="Claude Sonnet 4",
    model_id="anthropic.claude-sonnet-4-20250514-v1:0",
    active_patterns=None,
    app_profiles=None,
):
    """Build a model entry for the snapshot."""
    entry = {
        "display_name": display_name,
        "model_id": model_id,
        "active_patterns": active_patterns or [],
    }
    if app_profiles is not None:
        entry["app_profiles"] = app_profiles
    return entry


def _make_pattern(
    pattern_type="on-demand",
    geography=None,
    tpm_limit=100000,
    rpm_limit=1000,
    invocations_24h=500,
):
    """Build a pattern entry."""
    pattern = {
        "pattern_type": pattern_type,
        "quota_limits": {"tpm_limit": tpm_limit, "rpm_limit": rpm_limit},
        "invocations_24h": invocations_24h,
    }
    if geography:
        pattern["geography"] = geography
    return pattern


# ---------------------------------------------------------------------------
# Tests: Empty / Missing models
# ---------------------------------------------------------------------------


class TestEmptyModels:
    """When no models are found the function should report that clearly."""

    def test_empty_models_list(self):
        """Empty models list produces the 'No active Bedrock models' message."""
        snapshot = _make_snapshot(models=[])
        result = _format_for_llm(snapshot)

        assert "No active Bedrock models found." in result
        assert "us-east-1" in result
        assert "us-west-2" in result
        assert "2026-06-19T12:00:00Z" in result

    def test_none_models(self):
        """models=None is treated as empty."""
        snapshot = _make_snapshot(models=None)
        result = _format_for_llm(snapshot)

        assert "No active Bedrock models found." in result

    def test_empty_models_includes_assembled_at(self):
        """The assembled_at timestamp appears in the empty output."""
        snapshot = _make_snapshot(models=[], assembled_at="2026-01-15T08:30:00Z")
        result = _format_for_llm(snapshot)

        assert "2026-01-15T08:30:00Z" in result


# ---------------------------------------------------------------------------
# Tests: Single model with quota groups
# ---------------------------------------------------------------------------


class TestSingleModelWithQuotaGroups:
    """A single model with patterns that have quotas produces Quota Groups section."""

    def test_on_demand_pattern_generates_quota_group(self):
        """An on-demand pattern with tpm/rpm limits appears in Quota Groups."""
        model = _make_model(
            display_name="Claude Sonnet 4",
            active_patterns=[_make_pattern(tpm_limit=200000, rpm_limit=2000, invocations_24h=100)],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "## Quota Groups" in result
        assert "[Q1]" in result
        assert "on-demand" in result
        assert "Claude Sonnet 4" in result
        assert "200,000 TPM limit" in result
        assert "2,000 RPM limit" in result
        assert "100 invocations/24h" in result

    def test_model_inventory_section_present(self):
        """Model Inventory section is always generated when models exist."""
        model = _make_model(
            display_name="Nova Pro",
            active_patterns=[_make_pattern(tpm_limit=50000, rpm_limit=500)],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "## Model Inventory" in result
        assert "Nova Pro" in result

    def test_no_invocations_shows_no_recent_usage(self):
        """Zero invocations_24h shows 'no recent usage'."""
        model = _make_model(
            active_patterns=[_make_pattern(invocations_24h=0, tpm_limit=10000, rpm_limit=100)],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "no recent usage" in result

    def test_pattern_without_limits_no_quota_group(self):
        """Patterns with tpm_limit<=0 and rpm_limit<=0 don't create a Quota Group."""
        model = _make_model(
            active_patterns=[_make_pattern(tpm_limit=-1, rpm_limit=-1)],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "## Quota Groups" not in result
        assert "## Model Inventory" in result

    def test_tpm_only_unknown_rpm(self):
        """When rpm_limit <= 0, it shows 'RPM unknown'."""
        model = _make_model(
            active_patterns=[_make_pattern(tpm_limit=100000, rpm_limit=-1)],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "RPM unknown" in result
        assert "100,000 TPM limit" in result

    def test_rpm_only_unknown_tpm(self):
        """When tpm_limit <= 0, it shows 'TPM unknown'."""
        model = _make_model(
            active_patterns=[_make_pattern(tpm_limit=-1, rpm_limit=500)],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "TPM unknown" in result
        assert "500 RPM limit" in result


# ---------------------------------------------------------------------------
# Tests: Multiple models with different pattern types
# ---------------------------------------------------------------------------


class TestMultipleModelsPatternTypes:
    """Multiple models and pattern types produce correct labeling."""

    def test_cross_region_geo_pattern(self):
        """cross-region-geo with geography produces '<geography>. cross-region' label."""
        model = _make_model(
            display_name="Claude Opus 4",
            active_patterns=[
                _make_pattern(pattern_type="cross-region-geo", geography="US", tpm_limit=300000, rpm_limit=3000),
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "US. cross-region" in result
        assert "Claude Opus 4" in result

    def test_cross_region_global_pattern(self):
        """cross-region-global pattern produces 'global cross-region' label."""
        model = _make_model(
            display_name="Nova Pro",
            active_patterns=[
                _make_pattern(pattern_type="cross-region-global", tpm_limit=500000, rpm_limit=5000),
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "global cross-region" in result

    def test_multiple_models_multiple_patterns(self):
        """Multiple models each with different patterns produce correct output."""
        model_a = _make_model(
            display_name="Claude Sonnet 4",
            active_patterns=[
                _make_pattern(pattern_type="on-demand", tpm_limit=100000, rpm_limit=1000, invocations_24h=200),
                _make_pattern(pattern_type="cross-region-geo", geography="EU", tpm_limit=50000, rpm_limit=500, invocations_24h=0),
            ],
        )
        model_b = _make_model(
            display_name="Nova Lite",
            active_patterns=[
                _make_pattern(pattern_type="cross-region-global", tpm_limit=800000, rpm_limit=8000, invocations_24h=1000),
            ],
        )
        snapshot = _make_snapshot(models=[model_a, model_b])
        result = _format_for_llm(snapshot)

        # Should have Q1, Q2, Q3 (three groups with limits)
        assert "[Q1]" in result
        assert "[Q2]" in result
        assert "[Q3]" in result
        assert "Claude Sonnet 4" in result
        assert "Nova Lite" in result
        assert "on-demand" in result
        assert "EU. cross-region" in result
        assert "global cross-region" in result

    def test_model_inventory_shows_active_inactive(self):
        """Model inventory marks patterns as active/inactive based on invocations."""
        model = _make_model(
            display_name="Test Model",
            active_patterns=[
                _make_pattern(tpm_limit=100, rpm_limit=10, invocations_24h=50),
                _make_pattern(pattern_type="cross-region-global", tpm_limit=200, rpm_limit=20, invocations_24h=0),
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        # Model Inventory section should have both
        assert "active" in result
        assert "inactive" in result

    def test_model_inventory_references_quota_groups(self):
        """Model inventory entries reference their quota group indices."""
        model = _make_model(
            display_name="Claude Haiku 4.5",
            active_patterns=[
                _make_pattern(tpm_limit=100000, rpm_limit=1000, invocations_24h=10),
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        # The model inventory line should reference Q1
        lines = result.split("\n")
        inventory_lines = [line for line in lines if "Claude Haiku 4.5" in line and "Q1" in line]
        assert len(inventory_lines) >= 1


# ---------------------------------------------------------------------------
# Tests: Application Profiles
# ---------------------------------------------------------------------------


class TestAppProfiles:
    """Models with app_profiles produce the Application Profiles section."""

    def test_app_profiles_section_present(self):
        """When models have app_profiles, the section appears."""
        model = _make_model(
            display_name="Claude Sonnet 4",
            active_patterns=[_make_pattern(tpm_limit=100000, rpm_limit=1000)],
            app_profiles=[
                {"name": "my-app-profile", "wraps": "on-demand", "has_cw_data": True},
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "## Application Profiles" in result
        assert "my-app-profile" in result
        assert "Claude Sonnet 4" in result
        assert "on-demand" in result
        assert "active" in result

    def test_app_profile_no_recent_data(self):
        """app_profile with has_cw_data=False shows 'no recent data'."""
        model = _make_model(
            display_name="Nova Pro",
            active_patterns=[_make_pattern(tpm_limit=10000, rpm_limit=100)],
            app_profiles=[
                {"name": "idle-profile", "wraps": "cross-region", "has_cw_data": False},
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "no recent data" in result
        assert "idle-profile" in result

    def test_app_profiles_listed_as_consumers_in_quota_groups(self):
        """App profiles that match a pattern's wraps key appear as consumers."""
        model = _make_model(
            display_name="Claude Sonnet 4",
            active_patterns=[
                _make_pattern(pattern_type="on-demand", tpm_limit=100000, rpm_limit=1000),
            ],
            app_profiles=[
                {"name": "consumer-a", "wraps": "on-demand", "has_cw_data": True},
                {"name": "consumer-b", "wraps": "on-demand", "has_cw_data": False},
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "Consumers: consumer-a, consumer-b" in result

    def test_cross_region_app_profiles_as_consumers(self):
        """Cross-region patterns use 'cross-region' wraps key for consumers."""
        model = _make_model(
            display_name="Nova Lite",
            active_patterns=[
                _make_pattern(pattern_type="cross-region-geo", geography="US", tpm_limit=50000, rpm_limit=500),
            ],
            app_profiles=[
                {"name": "cross-consumer", "wraps": "cross-region", "has_cw_data": True},
                {"name": "on-demand-consumer", "wraps": "on-demand", "has_cw_data": True},
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        # cross-consumer should appear as consumer, on-demand-consumer should not
        assert "Consumers: cross-consumer" in result
        assert "on-demand-consumer" not in result.split("Consumers:")[1].split("\n")[0]

    def test_no_app_profiles_no_section(self):
        """Without app_profiles the section is omitted."""
        model = _make_model(
            display_name="Claude Sonnet 4",
            active_patterns=[_make_pattern(tpm_limit=100000, rpm_limit=1000)],
            app_profiles=[],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "## Application Profiles" not in result


# ---------------------------------------------------------------------------
# Tests: Missing / None fields handled gracefully
# ---------------------------------------------------------------------------


class TestMissingFields:
    """Missing or None fields should not crash the function."""

    def test_model_without_display_name_falls_back_to_model_id(self):
        """If display_name is missing, model_id is used."""
        model = {
            "model_id": "anthropic.claude-sonnet-4-20250514-v1:0",
            "active_patterns": [_make_pattern(tpm_limit=100, rpm_limit=10)],
        }
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "anthropic.claude-sonnet-4-20250514-v1:0" in result

    def test_model_without_model_id_or_display_name(self):
        """If both display_name and model_id are missing, uses 'Unknown'."""
        model = {
            "active_patterns": [_make_pattern(tpm_limit=100, rpm_limit=10)],
        }
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "Unknown" in result

    def test_pattern_without_pattern_type_defaults_to_on_demand(self):
        """Missing pattern_type defaults to 'on-demand'."""
        model = _make_model(
            active_patterns=[{"quota_limits": {"tpm_limit": 100, "rpm_limit": 10}, "invocations_24h": 5}],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "on-demand" in result

    def test_pattern_without_quota_limits(self):
        """Missing quota_limits dict should not crash."""
        model = _make_model(
            active_patterns=[{"pattern_type": "on-demand", "invocations_24h": 5}],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        # Should not crash; tpm/rpm default to -1 so no quota group
        assert "## Model Inventory" in result

    def test_pattern_without_invocations_24h(self):
        """Missing invocations_24h defaults to 0."""
        model = _make_model(
            active_patterns=[{"pattern_type": "on-demand", "quota_limits": {"tpm_limit": 100, "rpm_limit": 10}}],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "no recent usage" in result

    def test_snapshot_missing_assembled_at(self):
        """Missing assembled_at uses 'unknown'."""
        snapshot = {"models": [], "regions_scanned": ["us-east-1"]}
        result = _format_for_llm(snapshot)

        assert "unknown" in result

    def test_snapshot_missing_regions_scanned(self):
        """Missing regions_scanned does not crash."""
        snapshot = {"models": [], "assembled_at": "2026-06-19T00:00:00Z"}
        result = _format_for_llm(snapshot)

        assert "No active Bedrock models found." in result

    def test_app_profile_missing_name(self):
        """app_profile with missing name uses 'unnamed'."""
        model = _make_model(
            display_name="Claude Sonnet 4",
            active_patterns=[_make_pattern(tpm_limit=100, rpm_limit=10)],
            app_profiles=[
                {"wraps": "on-demand", "has_cw_data": True},
            ],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "unnamed" in result

    def test_model_with_empty_active_patterns(self):
        """A model with an empty active_patterns list still appears in inventory."""
        model = _make_model(
            display_name="Idle Model",
            active_patterns=[],
        )
        snapshot = _make_snapshot(models=[model])
        result = _format_for_llm(snapshot)

        assert "## Model Inventory" in result
        assert "Idle Model" in result


# ---------------------------------------------------------------------------
# Tests: Header / formatting
# ---------------------------------------------------------------------------


class TestHeaderFormatting:
    """Verify overall structure of the output."""

    def test_header_includes_date_prefix(self):
        """The header shows the first 10 chars of assembled_at (date portion)."""
        snapshot = _make_snapshot(
            models=[_make_model(active_patterns=[_make_pattern(tpm_limit=100, rpm_limit=10)])],
            assembled_at="2026-06-19T12:00:00Z",
        )
        result = _format_for_llm(snapshot)

        assert "# Customer Profile (updated 2026-06-19)" in result

    def test_regions_in_header(self):
        """Scanned regions appear after the header."""
        snapshot = _make_snapshot(
            models=[_make_model(active_patterns=[_make_pattern(tpm_limit=100, rpm_limit=10)])],
            regions=["eu-west-1", "ap-southeast-1"],
        )
        result = _format_for_llm(snapshot)

        assert "Regions: eu-west-1, ap-southeast-1" in result
