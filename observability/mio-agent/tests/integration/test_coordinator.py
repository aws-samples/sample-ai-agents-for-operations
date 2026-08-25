"""Integration tests for the MIO Agent coordinator orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mio_agent.coordinator.orchestrator import run_assessment, _group_findings_by_dimension
from mio_agent.models.assessment import AccessTier, AssessmentRequest, OutputAudience, TriggerType
from mio_agent.models.findings import Finding, FindingDimension, FindingSeverity

ACCOUNT_ID = "123456789012"
ROLE_ARN = "arn:aws:iam::123456789012:role/MIOAgentReadOnly"


def make_request(**overrides) -> AssessmentRequest:
    base = dict(
        account_id=ACCOUNT_ID,
        account_name="Test Account",
        access_tier=AccessTier.TIER3,
        role_arn=ROLE_ARN,
        trigger_type=TriggerType.ON_DEMAND,
        requested_by="test",
        output_audience=[OutputAudience.TAM],
    )
    base.update(overrides)
    return AssessmentRequest(**base)


def make_finding(severity=FindingSeverity.HIGH, dimension=FindingDimension.DISTRIBUTED_TRACING) -> Finding:
    return Finding(
        dimension=dimension,
        severity=severity,
        gap="Test gap",
        evidence="Test evidence",
        impact="Test impact",
        recommendation="Test recommendation",
    )


class TestGroupFindingsByDimension:
    def test_groups_correctly(self):
        findings = [
            make_finding(dimension=FindingDimension.DISTRIBUTED_TRACING),
            make_finding(dimension=FindingDimension.DISTRIBUTED_TRACING),
            make_finding(dimension=FindingDimension.ALERTING_QUALITY),
        ]
        grouped = _group_findings_by_dimension(findings)
        assert len(grouped["distributed_tracing"]) == 2
        assert len(grouped["alerting_quality"]) == 1
        assert len(grouped["metrics_coverage"]) == 0

    def test_all_dimensions_present(self):
        grouped = _group_findings_by_dimension([])
        assert set(grouped.keys()) == {
            "metrics_coverage", "alerting_quality", "log_intelligence",
            "distributed_tracing", "incident_readiness",
        }


class TestRunAssessment:
    @patch("mio_agent.coordinator.orchestrator.store_assessment")
    @patch("mio_agent.coordinator.orchestrator.get_assessment_history")
    @patch("mio_agent.coordinator.orchestrator.validate_third_party_coverage")
    @patch("mio_agent.coordinator.orchestrator.scan_live_stacks")
    @patch("mio_agent.coordinator.orchestrator.analyze_cloudwatch")
    @patch("mio_agent.coordinator.orchestrator.generate_tam_brief")
    def test_tier3_runs_all_agents(
        self,
        mock_tam_brief,
        mock_cw,
        mock_iac,
        mock_tp,
        mock_history,
        mock_store,
    ):
        mock_history.return_value = []
        mock_cw.return_value = [make_finding()]
        mock_iac.return_value = []
        mock_tp.return_value = []
        mock_tam_brief.return_value = MagicMock()
        mock_store.return_value = "test-id"

        request = make_request()
        result = run_assessment(request)

        assert result.oms is not None
        assert result.oms.account_id == ACCOUNT_ID
        assert result.oms.overall_oms >= 1.0
        assert result.oms.overall_oms <= 5.0
        mock_cw.assert_called_once()
        mock_tp.assert_called_once()

    @patch("mio_agent.coordinator.orchestrator.store_assessment")
    @patch("mio_agent.coordinator.orchestrator.get_assessment_history")
    def test_tier1_skips_live_agents(self, mock_history, mock_store):
        mock_history.return_value = []
        mock_store.return_value = "test-id"

        request = make_request(
            access_tier=AccessTier.TIER1,
            role_arn=None,
        )
        result = run_assessment(request)

        assert result.oms is not None
        assert result.oms.access_tier_used == AccessTier.TIER1
        assert result.oms.confidence.value == "LOW"

    @patch("mio_agent.coordinator.orchestrator.store_assessment")
    @patch("mio_agent.coordinator.orchestrator.get_assessment_history")
    @patch("mio_agent.coordinator.orchestrator.analyze_cloudwatch")
    @patch("mio_agent.coordinator.orchestrator.scan_live_stacks")
    @patch("mio_agent.coordinator.orchestrator.validate_third_party_coverage")
    def test_trend_calculated_from_history(
        self, mock_tp, mock_iac, mock_cw, mock_history, mock_store
    ):
        mock_history.return_value = [{"overall_oms": 2.0}]  # previous score
        mock_cw.return_value = []
        mock_iac.return_value = []
        mock_tp.return_value = []
        mock_store.return_value = "test-id"

        request = make_request(output_audience=[OutputAudience.TAM])
        result = run_assessment(request)

        # No findings = 5.0 OMS, previous was 2.0 → IMPROVING
        assert result.oms.trend == "IMPROVING"
        assert result.oms.previous_oms == 2.0

    @patch("mio_agent.coordinator.orchestrator.store_assessment")
    @patch("mio_agent.coordinator.orchestrator.get_assessment_history")
    @patch("mio_agent.coordinator.orchestrator.analyze_cloudwatch")
    @patch("mio_agent.coordinator.orchestrator.scan_live_stacks")
    @patch("mio_agent.coordinator.orchestrator.validate_third_party_coverage")
    def test_agent_failure_continues_gracefully(
        self, mock_tp, mock_iac, mock_cw, mock_history, mock_store
    ):
        mock_history.return_value = []
        mock_cw.side_effect = Exception("CloudWatch API unavailable")
        mock_iac.return_value = []
        mock_tp.return_value = []
        mock_store.return_value = "test-id"

        request = make_request()
        # Should not raise — graceful degradation
        result = run_assessment(request)
        assert result.oms is not None
