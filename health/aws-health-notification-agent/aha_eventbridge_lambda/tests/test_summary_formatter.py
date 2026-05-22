# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Unit tests for summary_formatter.format_summary() edge cases.

Requirements: 12.2, 12.3, 12.4
"""

from aha_eventbridge_lambda.summary_formatter import format_summary


def _base_result(**overrides):
    """Build a minimal Agent_Classification_Result dict."""
    base = {
        "classification_category": "BREAKING_CHANGE",
        "classification_reason": "API deprecation affecting production workloads",
        "affected_service": "CASSANDRA",
        "affected_accounts": [
            {"account_id": "123456789012", "environment_type": "production"},
            {"account_id": "987654321098", "environment_type": "staging"},
        ],
    }
    base.update(overrides)
    return base


class TestSummaryFormatterEdgeCases:
    """Req 12.2, 12.3, 12.4: Conditional sections based on input."""

    def test_no_impact_no_cost(self):
        """Only required sections when no optional fields present."""
        result = _base_result()
        summary = format_summary(result)

        assert "Classification: BREAKING_CHANGE" in summary
        assert "Reason: API deprecation" in summary
        assert "Affected Service: CASSANDRA" in summary
        assert "123456789012 (production)" in summary
        assert "987654321098 (staging)" in summary
        assert "Impact Analysis" not in summary
        assert "Cost Projection" not in summary

    def test_impact_analysis_without_cost(self):
        """Impact analysis section present, cost projection absent."""
        result = _base_result(
            impact_analysis={
                "summary": "Production keyspaces will become inaccessible",
                "risk_level": "HIGH",
                "action_required": True,
            },
        )
        summary = format_summary(result)

        assert "Impact Analysis" in summary
        assert "Production keyspaces will become inaccessible" in summary
        assert "Risk Level: HIGH" in summary
        assert "Action Required: Yes" in summary
        assert "Cost Projection" not in summary

    def test_cost_projection_without_impact(self):
        """Cost projection section present, impact analysis absent."""
        result = _base_result(
            cost_projection={"details": "$2,500/month additional compute costs"},
        )
        summary = format_summary(result)

        assert "Impact Analysis" not in summary
        assert "Cost Projection" in summary
        assert "$2,500/month additional compute costs" in summary

    def test_both_impact_and_cost(self):
        """Both optional sections present."""
        result = _base_result(
            impact_analysis={
                "summary": "High risk migration",
                "risk_level": "HIGH",
                "action_required": True,
            },
            cost_projection={"details": "$500/month"},
        )
        summary = format_summary(result)

        assert "Impact Analysis" in summary
        assert "High risk migration" in summary
        assert "Cost Projection" in summary
        assert "$500/month" in summary

    def test_action_required_false(self):
        """Action required is False → 'No' in output."""
        result = _base_result(
            impact_analysis={
                "summary": "Minor impact",
                "risk_level": "LOW",
                "action_required": False,
            },
        )
        summary = format_summary(result)
        assert "Action Required: No" in summary

    def test_empty_affected_accounts(self):
        """Empty affected accounts list produces section header only."""
        result = _base_result(affected_accounts=[])
        summary = format_summary(result)

        assert "Affected Accounts:" in summary
        assert "Classification: BREAKING_CHANGE" in summary

    def test_single_account(self):
        """Single account in the list."""
        result = _base_result(
            affected_accounts=[
                {"account_id": "111111111111", "environment_type": "development"},
            ],
        )
        summary = format_summary(result)
        assert "111111111111 (development)" in summary
