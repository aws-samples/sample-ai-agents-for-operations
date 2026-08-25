"""Unit tests for CloudWatch Analyst agent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mio_agent.agents.cloudwatch_analyst import (
    _analyze_alarms,
    _analyze_dashboards,
    _analyze_log_groups,
    _analyze_metrics_coverage,
    _analyze_tracing,
)
from mio_agent.models.assessment import AccessTier
from mio_agent.models.findings import FindingDimension, FindingSeverity

ACCOUNT_ID = "123456789012"
TIER = AccessTier.TIER3
ROLE_ARN = "arn:aws:iam::123456789012:role/MIOAgentReadOnly"


class TestAnalyzeAlarms:
    @patch("mio_agent.agents.cloudwatch_analyst.list_cloudwatch_alarms")
    def test_no_alarms_produces_critical_finding(self, mock_list):
        mock_list.return_value = []
        findings = _analyze_alarms(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        assert len(findings) == 1
        assert findings[0].severity == FindingSeverity.CRITICAL
        assert findings[0].dimension == FindingDimension.ALERTING_QUALITY

    @patch("mio_agent.agents.cloudwatch_analyst.list_cloudwatch_alarms")
    def test_alarms_with_no_actions_produces_high_finding(self, mock_list):
        mock_list.return_value = [
            {"Name": "alarm1", "StateValue": "OK", "AlarmActions": []},
            {"Name": "alarm2", "StateValue": "OK", "AlarmActions": []},
        ]
        findings = _analyze_alarms(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        high_findings = [f for f in findings if f.severity == FindingSeverity.HIGH]
        assert len(high_findings) >= 1

    @patch("mio_agent.agents.cloudwatch_analyst.list_cloudwatch_alarms")
    def test_good_alarms_no_critical_finding(self, mock_list):
        mock_list.return_value = [
            {"Name": "alarm1", "StateValue": "OK", "AlarmActions": ["arn:aws:sns:us-east-1:123456789012:alerts"]},
        ]
        findings = _analyze_alarms(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        critical = [f for f in findings if f.severity == FindingSeverity.CRITICAL]
        assert len(critical) == 0

    @patch("mio_agent.agents.cloudwatch_analyst.list_cloudwatch_alarms")
    def test_handles_exception_gracefully(self, mock_list):
        mock_list.side_effect = Exception("API error")
        findings = _analyze_alarms(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        assert findings == []


class TestAnalyzeMetricsCoverage:
    @patch("mio_agent.agents.cloudwatch_analyst.get_metrics_coverage")
    def test_lambda_without_tracing_produces_finding(self, mock_coverage):
        mock_coverage.return_value = {
            "lambda": {"total": 10, "with_xray_tracing": 2, "without_xray_tracing": 8},
            "ec2": {},
            "rds": {},
        }
        findings = _analyze_metrics_coverage(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        dt_findings = [f for f in findings if f.dimension == FindingDimension.DISTRIBUTED_TRACING]
        assert len(dt_findings) >= 1

    @patch("mio_agent.agents.cloudwatch_analyst.get_metrics_coverage")
    def test_full_lambda_tracing_no_finding(self, mock_coverage):
        mock_coverage.return_value = {
            "lambda": {"total": 10, "with_xray_tracing": 10, "without_xray_tracing": 0},
            "ec2": {},
            "rds": {},
        }
        findings = _analyze_metrics_coverage(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        dt_findings = [f for f in findings if f.dimension == FindingDimension.DISTRIBUTED_TRACING]
        assert len(dt_findings) == 0

    @patch("mio_agent.agents.cloudwatch_analyst.get_metrics_coverage")
    def test_high_severity_when_majority_missing_tracing(self, mock_coverage):
        mock_coverage.return_value = {
            "lambda": {"total": 10, "with_xray_tracing": 3, "without_xray_tracing": 7},
            "ec2": {},
            "rds": {},
        }
        findings = _analyze_metrics_coverage(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        high_findings = [f for f in findings if f.severity == FindingSeverity.HIGH]
        assert len(high_findings) >= 1


class TestAnalyzeLogGroups:
    @patch("mio_agent.agents.cloudwatch_analyst.analyze_log_groups")
    def test_no_retention_policy_produces_finding(self, mock_logs):
        mock_logs.return_value = {
            "total_log_groups": 5,
            "without_retention_policy": 3,
            "with_metric_filters": 2,
            "without_metric_filters": 3,
            "no_retention_examples": ["/aws/lambda/fn1"],
        }
        findings = _analyze_log_groups(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        assert len(findings) >= 1

    @patch("mio_agent.agents.cloudwatch_analyst.analyze_log_groups")
    def test_no_metric_filters_produces_high_finding(self, mock_logs):
        mock_logs.return_value = {
            "total_log_groups": 5,
            "without_retention_policy": 0,
            "with_metric_filters": 0,
            "without_metric_filters": 5,
            "no_retention_examples": [],
        }
        findings = _analyze_log_groups(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        high_findings = [f for f in findings if f.severity == FindingSeverity.HIGH]
        assert len(high_findings) >= 1


class TestAnalyzeDashboards:
    @patch("mio_agent.agents.cloudwatch_analyst.validate_dashboards")
    def test_no_dashboards_produces_high_finding(self, mock_dash):
        mock_dash.return_value = {
            "total_dashboards": 0,
            "has_dashboards": False,
            "dashboard_names": [],
        }
        findings = _analyze_dashboards(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        assert len(findings) == 1
        assert findings[0].severity == FindingSeverity.HIGH
        assert findings[0].dimension == FindingDimension.INCIDENT_READINESS

    @patch("mio_agent.agents.cloudwatch_analyst.validate_dashboards")
    def test_has_dashboards_no_finding(self, mock_dash):
        mock_dash.return_value = {
            "total_dashboards": 3,
            "has_dashboards": True,
            "dashboard_names": ["Operations", "Business", "Infrastructure"],
        }
        findings = _analyze_dashboards(ACCOUNT_ID, TIER, ROLE_ARN, "us-east-1")
        assert len(findings) == 0
