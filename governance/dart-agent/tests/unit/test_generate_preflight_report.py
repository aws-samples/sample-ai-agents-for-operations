# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for generate_preflight_report tool."""

from pathlib import Path
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))

from tools.generate_preflight_report import (
    _compute_quality_score,
    _determine_recommendation,
    _recommendation_emoji,
    _build_findings,
)


SAMPLE_PROFILE = {
    "format": "jsonl",
    "total_records": 50000,
    "encoding": "utf-8",
    "encoding_warning": False,
    "empty_record_count": 0,
    "detected_schema_pattern": "instruction_response",
}

SAMPLE_PROFILE_WITH_ISSUES = {
    "format": "jsonl",
    "total_records": 50000,
    "encoding": "Latin-1",
    "encoding_warning": True,
    "empty_record_count": 234,
    "detected_schema_pattern": None,
}

SAMPLE_DUPES_CLEAN = {
    "exact_duplicate_count": 0,
    "near_duplicate_count": 0,
    "duplicate_rate": 0.0,
}

SAMPLE_DUPES_WITH_ISSUES = {
    "exact_duplicate_count": 100,
    "near_duplicate_count": 6050,
    "duplicate_rate": 0.123,
}

SAMPLE_PII_CLEAN = {"pii_found": False, "affected_record_count": 0, "entity_type_counts": {}}

SAMPLE_PII_WITH_ISSUES = {
    "pii_found": True,
    "affected_record_count": 847,
    "entity_type_counts": {"EMAIL": 400, "PHONE": 447},
}

SAMPLE_VIABILITY_OK = {
    "token_budget": {"records_exceeding_context": 0, "model_context_limit": 200000},
    "schema_check": {"schema_valid": True, "matched_patterns": ["instruction_response"], "columns_found": ["instruction", "response"]},
    "size_check": {"meets_minimum": True, "total_records": 50000, "minimum_recommended": 500, "model_size_category": "default"},
    "leakage_check": {"leakage_checked": False},
    "blocking_issues": [],
    "warnings": [],
}

SAMPLE_COST_OK = {
    "cost_before_fixes": {"estimated_cost_usd": 18400},
    "cost_after_fixes": {"estimated_cost_usd": 14200},
    "savings_usd": 4200,
    "savings_percent": 22.8,
}


class TestComputeQualityScore:
    def test_no_findings_perfect_score(self):
        assert _compute_quality_score([]) == 100

    def test_critical_finding_reduces_score(self):
        findings = [{"severity": "critical"}]
        score = _compute_quality_score(findings)
        assert score == 70

    def test_multiple_findings_accumulate(self):
        findings = [
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "medium"},
        ]
        score = _compute_quality_score(findings)
        assert score == 100 - 30 - 15 - 7

    def test_score_never_below_zero(self):
        findings = [{"severity": "critical"}] * 10
        score = _compute_quality_score(findings)
        assert score == 0


class TestDetermineRecommendation:
    def test_no_findings_is_go(self):
        assert _determine_recommendation([]) == "GO"

    def test_only_low_findings_is_go(self):
        findings = [{"severity": "low"}, {"severity": "low"}]
        assert _determine_recommendation(findings) == "GO"

    def test_medium_finding_is_go_with_fixes(self):
        findings = [{"severity": "medium"}]
        assert _determine_recommendation(findings) == "GO WITH FIXES"

    def test_high_finding_is_go_with_fixes(self):
        findings = [{"severity": "high"}]
        assert _determine_recommendation(findings) == "GO WITH FIXES"

    def test_critical_finding_is_no_go(self):
        findings = [{"severity": "critical"}]
        assert _determine_recommendation(findings) == "NO-GO"

    def test_critical_overrides_low(self):
        findings = [{"severity": "low"}, {"severity": "critical"}]
        assert _determine_recommendation(findings) == "NO-GO"


class TestRecommendationEmoji:
    def test_go_emoji(self):
        assert _recommendation_emoji("GO") == "✅"

    def test_go_with_fixes_emoji(self):
        assert _recommendation_emoji("GO WITH FIXES") == "⚠️"

    def test_no_go_emoji(self):
        assert _recommendation_emoji("NO-GO") == "🔴"


class TestBuildFindings:
    def test_clean_dataset_no_findings(self):
        findings = _build_findings(
            SAMPLE_PROFILE, SAMPLE_DUPES_CLEAN, SAMPLE_PII_CLEAN,
            SAMPLE_VIABILITY_OK, SAMPLE_COST_OK,
        )
        assert len(findings) == 0

    def test_pii_generates_critical_finding(self):
        findings = _build_findings(
            SAMPLE_PROFILE, SAMPLE_DUPES_CLEAN, SAMPLE_PII_WITH_ISSUES,
            SAMPLE_VIABILITY_OK, SAMPLE_COST_OK,
        )
        assert any(f["severity"] == "critical" and "PII" in f["id"] for f in findings)

    def test_duplicates_generate_high_finding(self):
        findings = _build_findings(
            SAMPLE_PROFILE, SAMPLE_DUPES_WITH_ISSUES, SAMPLE_PII_CLEAN,
            SAMPLE_VIABILITY_OK, SAMPLE_COST_OK,
        )
        assert any(f["severity"] == "high" and "DEDUP" in f["id"] for f in findings)

    def test_empty_records_generate_high_finding(self):
        findings = _build_findings(
            SAMPLE_PROFILE_WITH_ISSUES, SAMPLE_DUPES_CLEAN, SAMPLE_PII_CLEAN,
            SAMPLE_VIABILITY_OK, SAMPLE_COST_OK,
        )
        assert any("EMPTY" in f["id"] for f in findings)

    def test_findings_sorted_by_severity(self):
        findings = _build_findings(
            SAMPLE_PROFILE_WITH_ISSUES, SAMPLE_DUPES_WITH_ISSUES, SAMPLE_PII_WITH_ISSUES,
            SAMPLE_VIABILITY_OK, SAMPLE_COST_OK,
        )
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        orders = [severity_order[f["severity"]] for f in findings]
        assert orders == sorted(orders)

    def test_auto_fixable_flags_set(self):
        findings = _build_findings(
            SAMPLE_PROFILE_WITH_ISSUES, SAMPLE_DUPES_WITH_ISSUES, SAMPLE_PII_WITH_ISSUES,
            SAMPLE_VIABILITY_OK, SAMPLE_COST_OK,
        )
        # PII should NOT be auto-fixable
        pii_findings = [f for f in findings if "PII" in f["id"]]
        assert all(not f["auto_fixable"] for f in pii_findings)
        # Encoding should be auto-fixable
        enc_findings = [f for f in findings if "ENC" in f["id"]]
        assert all(f["auto_fixable"] for f in enc_findings)
