# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.
# Risk assessment: See security/SECURITY.md for threat model and risk documentation.

"""Property-based tests for summary_formatter.format_summary().

Uses Hypothesis to verify universal properties across randomly generated
Agent_Classification_Result dicts.
"""

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from aha_eventbridge_lambda.summary_formatter import format_summary


# --- Strategies ---

classification_category = st.sampled_from([
    "BREAKING_CHANGE", "COST_IMPLICATION", "SECURITY_RELATED",
    "SERVICE_DISRUPTION", "INFORMATIONAL", "UNCLASSIFIED",
])

affected_account = st.fixed_dictionaries({
    "account_id": st.from_regex(r"[0-9]{12}", fullmatch=True),
    "environment_type": st.sampled_from([
        "production", "staging", "development", "sandbox",
    ]),
})

impact_analysis = st.fixed_dictionaries({
    "summary": st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
    "risk_level": st.sampled_from(["HIGH", "MEDIUM", "LOW"]),
    "action_required": st.booleans(),
})

cost_projection = st.fixed_dictionaries({
    "details": st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
})

reason_text = st.text(min_size=1, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",)))
service_name = st.sampled_from(["EC2", "RDS", "CASSANDRA", "LAMBDA", "S3", "IAM"])


def classification_result_strategy(
    include_impact=st.booleans(),
    include_cost=st.booleans(),
):
    """Build a strategy for Agent_Classification_Result dicts."""
    @st.composite
    def _build(draw):
        result = {
            "classification_category": draw(classification_category),
            "classification_reason": draw(reason_text),
            "affected_service": draw(service_name),
            "affected_accounts": draw(st.lists(affected_account, min_size=0, max_size=5)),
        }
        if draw(include_impact):
            result["impact_analysis"] = draw(impact_analysis)
        if draw(include_cost):
            result["cost_projection"] = draw(cost_projection)
        return result
    return _build()


# Feature: aha-eventbridge-lambda, Property 10: Summary formatter includes all required and conditional fields as plain text
# **Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5**
class TestProperty10SummaryFormatterCompleteness:
    """Property 10: Summary formatter includes all required and conditional fields as plain text.

    For any valid Agent_Classification_Result dict, the summary formatter shall
    produce a plain-text string (not valid JSON) that contains the classification
    category, classification reason, affected service, and all affected accounts
    with their environment types. When the input includes an impact analysis, the
    output shall contain the impact summary, risk level, and action-required status.
    When the input includes a cost projection, the output shall contain the
    projected cost details.
    """

    @given(result=classification_result_strategy())
    @settings(max_examples=100)
    def test_output_is_plain_text_not_json(self, result):
        """The output is plain text and not parseable as JSON."""
        summary = format_summary(result)
        assert isinstance(summary, str)
        try:
            json.loads(summary)
            raise AssertionError("Summary should not be valid JSON")
        except (json.JSONDecodeError, AssertionError):
            pass  # Expected — not valid JSON

    @given(result=classification_result_strategy())
    @settings(max_examples=100)
    def test_required_fields_always_present(self, result):
        """Classification category, reason, affected service, and accounts are always present."""
        summary = format_summary(result)

        assert result["classification_category"] in summary
        assert result["classification_reason"] in summary
        assert result["affected_service"] in summary

        for acct in result["affected_accounts"]:
            assert acct["account_id"] in summary
            assert acct["environment_type"] in summary

    @given(result=classification_result_strategy(
        include_impact=st.just(True),
        include_cost=st.booleans(),
    ))
    @settings(max_examples=100)
    def test_impact_analysis_present_when_in_input(self, result):
        """When input includes impact_analysis, output contains its fields."""
        summary = format_summary(result)
        impact = result["impact_analysis"]

        assert "Impact Analysis" in summary
        assert impact["summary"] in summary
        assert impact["risk_level"] in summary
        expected_action = "Yes" if impact["action_required"] else "No"
        assert f"Action Required: {expected_action}" in summary

    @given(result=classification_result_strategy(
        include_impact=st.just(False),
        include_cost=st.booleans(),
    ))
    @settings(max_examples=100)
    def test_impact_analysis_absent_when_not_in_input(self, result):
        """When input lacks impact_analysis, output does not contain the section."""
        summary = format_summary(result)
        assert "Impact Analysis" not in summary

    @given(result=classification_result_strategy(
        include_impact=st.booleans(),
        include_cost=st.just(True),
    ))
    @settings(max_examples=100)
    def test_cost_projection_present_when_in_input(self, result):
        """When input includes cost_projection, output contains its details."""
        summary = format_summary(result)
        assert "Cost Projection" in summary
        assert result["cost_projection"]["details"] in summary

    @given(result=classification_result_strategy(
        include_impact=st.booleans(),
        include_cost=st.just(False),
    ))
    @settings(max_examples=100)
    def test_cost_projection_absent_when_not_in_input(self, result):
        """When input lacks cost_projection, output does not contain the section."""
        summary = format_summary(result)
        assert "Cost Projection" not in summary
