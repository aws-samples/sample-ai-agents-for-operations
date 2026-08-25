"""Unit tests for OMS scoring engine."""

from __future__ import annotations

import pytest

from mio_agent.coordinator.scoring import (
    build_oms,
    calculate_dimension_score,
    calculate_oms,
    calculate_trend,
    classify_risk_level,
)
from mio_agent.models.assessment import AccessTier, RiskLevel
from mio_agent.models.findings import Finding, FindingDimension, FindingSeverity

VALID_ACCOUNT_ID = "123456789012"


def _make_finding(severity: FindingSeverity, dimension: FindingDimension = FindingDimension.METRICS_COVERAGE) -> Finding:
    return Finding(
        dimension=dimension,
        severity=severity,
        gap=f"Test gap ({severity.value})",
        evidence="Test evidence",
        impact="Test impact",
        recommendation="Test recommendation",
    )


class TestCalculateDimensionScore:
    def test_no_findings_returns_max(self):
        assert calculate_dimension_score([], resource_count=10) == 5.0

    def test_single_critical_finding(self):
        findings = [_make_finding(FindingSeverity.CRITICAL)]
        score = calculate_dimension_score(findings, resource_count=1)
        assert 1.0 <= score <= 5.0

    def test_more_resources_less_penalty(self):
        findings = [_make_finding(FindingSeverity.HIGH)]
        score_small = calculate_dimension_score(findings, resource_count=1)
        score_large = calculate_dimension_score(findings, resource_count=100)
        assert score_large > score_small

    def test_score_never_below_minimum(self):
        findings = [_make_finding(FindingSeverity.CRITICAL) for _ in range(20)]
        score = calculate_dimension_score(findings, resource_count=1)
        assert score >= 1.0

    def test_score_never_above_maximum(self):
        score = calculate_dimension_score([], resource_count=0)
        assert score <= 5.0

    def test_zero_resource_count_handled(self):
        findings = [_make_finding(FindingSeverity.HIGH)]
        score = calculate_dimension_score(findings, resource_count=0)
        assert 1.0 <= score <= 5.0


class TestCalculateOMS:
    def test_all_perfect_scores(self):
        scores = {
            "metrics_coverage": 5.0,
            "alerting_quality": 5.0,
            "log_intelligence": 5.0,
            "distributed_tracing": 5.0,
            "incident_readiness": 5.0,
        }
        assert calculate_oms(scores) == 5.0

    def test_all_minimum_scores(self):
        scores = {
            "metrics_coverage": 1.0,
            "alerting_quality": 1.0,
            "log_intelligence": 1.0,
            "distributed_tracing": 1.0,
            "incident_readiness": 1.0,
        }
        assert calculate_oms(scores) == 1.0

    def test_weighted_average_correct(self):
        # Weights: mc=0.25, aq=0.25, li=0.20, dt=0.20, ir=0.10
        scores = {
            "metrics_coverage": 4.0,
            "alerting_quality": 2.0,
            "log_intelligence": 3.0,
            "distributed_tracing": 2.0,
            "incident_readiness": 5.0,
        }
        expected = round(4.0 * 0.25 + 2.0 * 0.25 + 3.0 * 0.20 + 2.0 * 0.20 + 5.0 * 0.10, 1)
        assert calculate_oms(scores) == expected

    def test_missing_dimension_raises(self):
        with pytest.raises(ValueError, match="Missing required dimensions"):
            calculate_oms({"metrics_coverage": 3.0})

    def test_result_rounded_to_one_decimal(self):
        scores = {
            "metrics_coverage": 3.3,
            "alerting_quality": 3.3,
            "log_intelligence": 3.3,
            "distributed_tracing": 3.3,
            "incident_readiness": 3.3,
        }
        result = calculate_oms(scores)
        assert result == round(result, 1)


class TestClassifyRiskLevel:
    def test_low_risk_at_4_0(self):
        assert classify_risk_level(4.0) == RiskLevel.LOW

    def test_low_risk_at_5_0(self):
        assert classify_risk_level(5.0) == RiskLevel.LOW

    def test_medium_risk_at_3_0(self):
        assert classify_risk_level(3.0) == RiskLevel.MEDIUM

    def test_medium_risk_at_3_9(self):
        assert classify_risk_level(3.9) == RiskLevel.MEDIUM

    def test_high_risk_at_2_0(self):
        assert classify_risk_level(2.0) == RiskLevel.HIGH

    def test_high_risk_at_2_9(self):
        assert classify_risk_level(2.9) == RiskLevel.HIGH

    def test_critical_risk_at_1_0(self):
        assert classify_risk_level(1.0) == RiskLevel.CRITICAL

    def test_critical_risk_at_1_9(self):
        assert classify_risk_level(1.9) == RiskLevel.CRITICAL


class TestCalculateTrend:
    def test_no_previous_returns_none(self):
        assert calculate_trend(3.0, None) is None

    def test_improving_trend(self):
        assert calculate_trend(3.5, 3.0) == "IMPROVING"

    def test_declining_trend(self):
        assert calculate_trend(2.5, 3.0) == "DECLINING"

    def test_stable_trend_small_delta(self):
        assert calculate_trend(3.1, 3.0) == "STABLE"

    def test_stable_trend_exact_threshold(self):
        # 3.2 - 3.0 in float = 0.19999... which is < 0.2 threshold → STABLE
        assert calculate_trend(3.19, 3.0) == "STABLE"
        # Clearly within stable range
        assert calculate_trend(3.1, 3.0) == "STABLE"

    def test_improving_just_above_threshold(self):
        assert calculate_trend(3.21, 3.0) == "IMPROVING"


class TestBuildOMS:
    def test_build_oms_no_findings(self):
        empty_findings: dict[str, list[Finding]] = {
            "metrics_coverage": [],
            "alerting_quality": [],
            "log_intelligence": [],
            "distributed_tracing": [],
            "incident_readiness": [],
        }
        oms = build_oms(
            assessment_id="test-001",
            account_id=VALID_ACCOUNT_ID,
            account_name="Test Account",
            findings_by_dimension=empty_findings,
            access_tier=AccessTier.TIER3,
        )
        assert oms.overall_oms == 5.0
        assert oms.risk_level == RiskLevel.LOW

    def test_build_oms_with_findings(self):
        findings: dict[str, list[Finding]] = {
            "metrics_coverage": [_make_finding(FindingSeverity.CRITICAL, FindingDimension.METRICS_COVERAGE)],
            "alerting_quality": [_make_finding(FindingSeverity.HIGH, FindingDimension.ALERTING_QUALITY)],
            "log_intelligence": [],
            "distributed_tracing": [],
            "incident_readiness": [],
        }
        oms = build_oms(
            assessment_id="test-002",
            account_id=VALID_ACCOUNT_ID,
            account_name="Test Account",
            findings_by_dimension=findings,
            access_tier=AccessTier.TIER3,
        )
        assert oms.overall_oms < 5.0
        assert oms.total_findings == 2

    def test_build_oms_trend_with_previous(self):
        empty_findings: dict[str, list[Finding]] = {
            "metrics_coverage": [],
            "alerting_quality": [],
            "log_intelligence": [],
            "distributed_tracing": [],
            "incident_readiness": [],
        }
        oms = build_oms(
            assessment_id="test-003",
            account_id=VALID_ACCOUNT_ID,
            account_name="Test Account",
            findings_by_dimension=empty_findings,
            access_tier=AccessTier.TIER3,
            previous_oms=3.0,
        )
        assert oms.trend == "IMPROVING"
        assert oms.previous_oms == 3.0
