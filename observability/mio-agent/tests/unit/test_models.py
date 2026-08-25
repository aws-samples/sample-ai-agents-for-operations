"""Unit tests for MIO Agent data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mio_agent.models.assessment import (
    AccessTier,
    AssessmentRequest,
    ConfidenceLevel,
    DimensionScore,
    OMS,
    RiskLevel,
    TriggerType,
)
from mio_agent.models.findings import (
    Finding,
    FindingCollection,
    FindingDimension,
    FindingEffort,
    FindingSeverity,
)
from mio_agent.models.reports import (
    AccountRiskSummary,
    ActionItem,
    CustomerReport,
    LeadershipSummary,
    TAMBrief,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_ACCOUNT_ID = "123456789012"
VALID_ROLE_ARN = "arn:aws:iam::123456789012:role/MIOAgentReadOnly"

VALID_DIMENSIONS = {
    "metrics_coverage": DimensionScore(score=3.2, weight=0.25),
    "alerting_quality": DimensionScore(score=2.1, weight=0.25),
    "log_intelligence": DimensionScore(score=3.5, weight=0.20),
    "distributed_tracing": DimensionScore(score=1.8, weight=0.20),
    "incident_readiness": DimensionScore(score=3.0, weight=0.10),
}


def make_assessment_request(**overrides) -> dict:
    base = {
        "account_id": VALID_ACCOUNT_ID,
        "account_name": "Acme Corp Production",
        "access_tier": AccessTier.TIER3,
        "role_arn": VALID_ROLE_ARN,
        "trigger_type": TriggerType.ON_DEMAND,
        "requested_by": "tam-alias",
    }
    base.update(overrides)
    return base


def make_oms(**overrides) -> dict:
    base = {
        "assessment_id": "test-assessment-001",
        "account_id": VALID_ACCOUNT_ID,
        "account_name": "Acme Corp",
        "overall_oms": 2.8,
        "dimensions": VALID_DIMENSIONS,
        "risk_level": RiskLevel.HIGH,
        "access_tier_used": AccessTier.TIER3,
        "confidence": ConfidenceLevel.HIGH,
    }
    base.update(overrides)
    return base


def make_finding(**overrides) -> dict:
    base = {
        "dimension": FindingDimension.DISTRIBUTED_TRACING,
        "severity": FindingSeverity.HIGH,
        "resource_arn": f"arn:aws:lambda:us-east-1:{VALID_ACCOUNT_ID}:function:order-processor-prod",
        "resource_type": "AWS::Lambda::Function",
        "gap": "X-Ray tracing not enabled",
        "evidence": "ActiveTracing=false in function configuration",
        "impact": "Cannot trace requests across service boundaries. Adds ~22 minutes to incident MTTD.",
        "recommendation": "Enable X-Ray active tracing on this Lambda function.",
        "effort": FindingEffort.LOW,
        "aws_service_recommendation": "AWS X-Ray",
        "documentation_url": "https://docs.aws.amazon.com/lambda/latest/dg/services-xray.html",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# AssessmentRequest tests
# ---------------------------------------------------------------------------

class TestAssessmentRequest:
    def test_valid_tier3_request(self):
        req = AssessmentRequest(**make_assessment_request())
        assert req.account_id == VALID_ACCOUNT_ID
        assert req.access_tier == AccessTier.TIER3
        assert req.assessment_id  # auto-generated UUID

    def test_auto_generates_assessment_id(self):
        req1 = AssessmentRequest(**make_assessment_request())
        req2 = AssessmentRequest(**make_assessment_request())
        assert req1.assessment_id != req2.assessment_id

    def test_valid_tier1_no_role_arn(self):
        req = AssessmentRequest(**make_assessment_request(
            access_tier=AccessTier.TIER1,
            role_arn=None,
        ))
        assert req.role_arn is None

    def test_tier3_requires_role_arn(self):
        with pytest.raises(ValidationError, match="role_arn is required"):
            AssessmentRequest(**make_assessment_request(role_arn=None))

    def test_invalid_account_id_too_short(self):
        with pytest.raises(ValidationError, match="account_id must be exactly 12 digits"):
            AssessmentRequest(**make_assessment_request(account_id="12345"))

    def test_invalid_account_id_with_letters(self):
        with pytest.raises(ValidationError, match="account_id must be exactly 12 digits"):
            AssessmentRequest(**make_assessment_request(account_id="12345678901a"))

    def test_invalid_role_arn(self):
        with pytest.raises(ValidationError, match="role_arn must be a valid IAM role ARN"):
            AssessmentRequest(**make_assessment_request(role_arn="not-an-arn"))

    def test_default_output_audience(self):
        req = AssessmentRequest(**make_assessment_request())
        assert len(req.output_audience) == 1

    def test_all_trigger_types(self):
        for trigger in TriggerType:
            req = AssessmentRequest(**make_assessment_request(trigger_type=trigger))
            assert req.trigger_type == trigger

    def test_trigger_context_stored(self):
        ctx = {"support_case_id": "case-123"}
        req = AssessmentRequest(**make_assessment_request(trigger_context=ctx))
        assert req.trigger_context["support_case_id"] == "case-123"


# ---------------------------------------------------------------------------
# DimensionScore tests
# ---------------------------------------------------------------------------

class TestDimensionScore:
    def test_valid_score(self):
        ds = DimensionScore(score=3.5, weight=0.25)
        assert ds.score == 3.5

    def test_score_rounded_to_one_decimal(self):
        ds = DimensionScore(score=3.567, weight=0.25)
        assert ds.score == 3.6

    def test_score_minimum(self):
        ds = DimensionScore(score=1.0, weight=0.25)
        assert ds.score == 1.0

    def test_score_maximum(self):
        ds = DimensionScore(score=5.0, weight=0.25)
        assert ds.score == 5.0

    def test_score_below_minimum_rejected(self):
        with pytest.raises(ValidationError):
            DimensionScore(score=0.9, weight=0.25)

    def test_score_above_maximum_rejected(self):
        with pytest.raises(ValidationError):
            DimensionScore(score=5.1, weight=0.25)


# ---------------------------------------------------------------------------
# OMS tests
# ---------------------------------------------------------------------------

class TestOMS:
    def test_valid_oms(self):
        oms = OMS(**make_oms())
        assert oms.overall_oms == 2.8
        assert oms.risk_level == RiskLevel.HIGH

    def test_overall_oms_rounded(self):
        oms = OMS(**make_oms(overall_oms=2.849))
        assert oms.overall_oms == 2.8

    def test_missing_dimension_rejected(self):
        dims = {k: v for k, v in VALID_DIMENSIONS.items() if k != "distributed_tracing"}
        with pytest.raises(ValidationError, match="Missing required dimensions"):
            OMS(**make_oms(dimensions=dims))

    def test_dimension_weights_must_sum_to_one(self):
        bad_dims = {
            "metrics_coverage": DimensionScore(score=3.0, weight=0.30),
            "alerting_quality": DimensionScore(score=3.0, weight=0.30),
            "log_intelligence": DimensionScore(score=3.0, weight=0.20),
            "distributed_tracing": DimensionScore(score=3.0, weight=0.20),
            "incident_readiness": DimensionScore(score=3.0, weight=0.20),
        }
        with pytest.raises(ValidationError, match="Dimension weights must sum to 1.0"):
            OMS(**make_oms(dimensions=bad_dims))

    def test_invalid_account_id(self):
        with pytest.raises(ValidationError):
            OMS(**make_oms(account_id="bad-id"))

    def test_invalid_trend(self):
        with pytest.raises(ValidationError, match="trend must be one of"):
            OMS(**make_oms(trend="UP"))

    def test_valid_trends(self):
        for trend in ["IMPROVING", "DECLINING", "STABLE", None]:
            oms = OMS(**make_oms(trend=trend))
            assert oms.trend == trend

    def test_derive_risk_level_low(self):
        assert OMS.derive_risk_level(4.5) == RiskLevel.LOW

    def test_derive_risk_level_medium(self):
        assert OMS.derive_risk_level(3.2) == RiskLevel.MEDIUM

    def test_derive_risk_level_high(self):
        assert OMS.derive_risk_level(2.5) == RiskLevel.HIGH

    def test_derive_risk_level_critical(self):
        assert OMS.derive_risk_level(1.5) == RiskLevel.CRITICAL

    def test_derive_risk_level_boundary_4(self):
        assert OMS.derive_risk_level(4.0) == RiskLevel.LOW

    def test_derive_risk_level_boundary_3(self):
        assert OMS.derive_risk_level(3.0) == RiskLevel.MEDIUM

    def test_derive_confidence_tier1(self):
        assert OMS.derive_confidence(AccessTier.TIER1) == ConfidenceLevel.LOW

    def test_derive_confidence_tier2(self):
        assert OMS.derive_confidence(AccessTier.TIER2) == ConfidenceLevel.MEDIUM

    def test_derive_confidence_tier3(self):
        assert OMS.derive_confidence(AccessTier.TIER3) == ConfidenceLevel.HIGH


# ---------------------------------------------------------------------------
# Finding tests
# ---------------------------------------------------------------------------

class TestFinding:
    def test_valid_finding(self):
        f = Finding(**make_finding())
        assert f.gap == "X-Ray tracing not enabled"
        assert f.severity == FindingSeverity.HIGH
        assert f.finding_id  # auto-generated

    def test_auto_generates_finding_id(self):
        f1 = Finding(**make_finding())
        f2 = Finding(**make_finding())
        assert f1.finding_id != f2.finding_id

    def test_invalid_resource_arn(self):
        with pytest.raises(ValidationError, match="resource_arn must start with 'arn:'"):
            Finding(**make_finding(resource_arn="not-an-arn"))

    def test_invalid_documentation_url(self):
        with pytest.raises(ValidationError, match="documentation_url must start with 'https://'"):
            Finding(**make_finding(documentation_url="http://example.com"))

    def test_severity_weight_critical(self):
        f = Finding(**make_finding(severity=FindingSeverity.CRITICAL))
        assert f.severity_weight == 2.0

    def test_severity_weight_high(self):
        f = Finding(**make_finding(severity=FindingSeverity.HIGH))
        assert f.severity_weight == 1.0

    def test_severity_weight_info(self):
        f = Finding(**make_finding(severity=FindingSeverity.INFO))
        assert f.severity_weight == 0.0

    def test_no_resource_arn_allowed(self):
        f = Finding(**make_finding(resource_arn=None))
        assert f.resource_arn is None

    def test_all_dimensions_valid(self):
        for dim in FindingDimension:
            f = Finding(**make_finding(dimension=dim))
            assert f.dimension == dim

    def test_all_severities_valid(self):
        for sev in FindingSeverity:
            f = Finding(**make_finding(severity=sev))
            assert f.severity == sev

    def test_all_efforts_valid(self):
        for effort in FindingEffort:
            f = Finding(**make_finding(effort=effort))
            assert f.effort == effort


# ---------------------------------------------------------------------------
# FindingCollection tests
# ---------------------------------------------------------------------------

class TestFindingCollection:
    def _make_collection(self) -> FindingCollection:
        fc = FindingCollection(assessment_id="test-001", account_id=VALID_ACCOUNT_ID)
        fc.add(Finding(**make_finding(severity=FindingSeverity.CRITICAL)))
        fc.add(Finding(**make_finding(severity=FindingSeverity.HIGH)))
        fc.add(Finding(**make_finding(severity=FindingSeverity.HIGH)))
        fc.add(Finding(**make_finding(
            severity=FindingSeverity.MEDIUM,
            dimension=FindingDimension.ALERTING_QUALITY,
        )))
        fc.add(Finding(**make_finding(
            severity=FindingSeverity.LOW,
            dimension=FindingDimension.LOG_INTELLIGENCE,
        )))
        return fc

    def test_critical_count(self):
        fc = self._make_collection()
        assert fc.critical_count == 1

    def test_high_count(self):
        fc = self._make_collection()
        assert fc.high_count == 2

    def test_top_3_returns_highest_severity(self):
        fc = self._make_collection()
        top3 = fc.top_3
        assert len(top3) == 3
        assert top3[0].severity == FindingSeverity.CRITICAL

    def test_by_dimension_grouping(self):
        fc = self._make_collection()
        by_dim = fc.by_dimension
        assert len(by_dim[FindingDimension.DISTRIBUTED_TRACING]) == 3
        assert len(by_dim[FindingDimension.ALERTING_QUALITY]) == 1

    def test_filter_by_severity(self):
        fc = self._make_collection()
        high_findings = fc.filter_by_severity(FindingSeverity.HIGH)
        assert len(high_findings) == 2

    def test_filter_by_dimension(self):
        fc = self._make_collection()
        log_findings = fc.filter_by_dimension(FindingDimension.LOG_INTELLIGENCE)
        assert len(log_findings) == 1

    def test_empty_collection(self):
        fc = FindingCollection(assessment_id="test-001", account_id=VALID_ACCOUNT_ID)
        assert fc.critical_count == 0
        assert fc.top_3 == []


# ---------------------------------------------------------------------------
# TAMBrief tests
# ---------------------------------------------------------------------------

class TestTAMBrief:
    def _make_brief(self) -> TAMBrief:
        return TAMBrief(
            report_id="report-001",
            assessment_id="assessment-001",
            account_id=VALID_ACCOUNT_ID,
            account_name="Acme Corp",
            current_oms=2.8,
            previous_oms=3.1,
            trend="DECLINING",
            risk_level=RiskLevel.HIGH,
            talking_points=["Enable X-Ray tracing", "Add CloudWatch alarms"],
        )

    def test_valid_tam_brief(self):
        brief = self._make_brief()
        assert brief.current_oms == 2.8
        assert brief.risk_level == RiskLevel.HIGH

    def test_trend_emoji_declining(self):
        brief = self._make_brief()
        assert brief.trend_emoji == "📉"

    def test_trend_emoji_improving(self):
        brief = self._make_brief()
        brief.trend = "IMPROVING"
        assert brief.trend_emoji == "📈"

    def test_risk_emoji_high(self):
        brief = self._make_brief()
        assert brief.risk_emoji == "🟠"

    def test_risk_emoji_critical(self):
        brief = self._make_brief()
        brief.risk_level = RiskLevel.CRITICAL
        assert brief.risk_emoji == "🔴"


# ---------------------------------------------------------------------------
# LeadershipSummary tests
# ---------------------------------------------------------------------------

class TestLeadershipSummary:
    def _make_summary(self) -> LeadershipSummary:
        accounts = [
            AccountRiskSummary(
                account_id=f"1234567890{i:02d}",
                account_name=f"Customer {i}",
                oms_score=2.5,
                risk_level=RiskLevel.HIGH,
            )
            for i in range(3)
        ]
        return LeadershipSummary(
            report_id="leadership-001",
            total_accounts=10,
            accounts_by_risk={"LOW": 3, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 2},
            average_oms=3.1,
            high_risk_accounts=accounts,
        )

    def test_critical_account_count(self):
        summary = self._make_summary()
        assert summary.critical_account_count == 2

    def test_at_risk_percentage(self):
        summary = self._make_summary()
        assert summary.at_risk_percentage == 50.0

    def test_zero_accounts_at_risk_percentage(self):
        summary = LeadershipSummary(
            report_id="test",
            total_accounts=0,
            accounts_by_risk={},
            average_oms=3.0,
        )
        assert summary.at_risk_percentage == 0.0
