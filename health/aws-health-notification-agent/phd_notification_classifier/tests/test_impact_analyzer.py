# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT-0 License
# See LICENSE file in the project root for full license information.
# Security scan: See security/SCAN_PROCESS.md for scan results and attestation.

"""Unit tests for the impact analyzer tool.

Requirements covered: 9.1, 9.2, 9.3, 9.4
"""

from __future__ import annotations

import pytest

from phd_notification_classifier.tools.impact_analyzer import analyze_impact

_analyze = analyze_impact._tool_func


def _notif(
    arn: str = "arn:aws:health:us-east-1::event/EKS/123",
    service: str = "EKS",
    event_type: str = "AWS_EKS_PLANNED_LIFECYCLE_EVENT",
    description: str = "EKS version will be deprecated",
) -> dict:
    return {
        "arn": arn,
        "service": service,
        "eventTypeCode": event_type,
        "eventDescription": description,
    }


def _prod_account(
    account_id: str = "111111111111",
    resources: list | None = None,
) -> dict:
    return {
        "account_id": account_id,
        "account_name": f"prod-{account_id[:4]}",
        "environment_type": "production",
        "affected_resources": resources or [],
    }


def _non_prod_account(
    account_id: str = "222222222222",
    resources: list | None = None,
) -> dict:
    return {
        "account_id": account_id,
        "account_name": f"dev-{account_id[:4]}",
        "environment_type": "non-production",
        "affected_resources": resources or [],
    }


# ---------------------------------------------------------------------------
# Req 9.1, 9.2 – BREAKING_CHANGE with prod accounts: high risk + action required
# ---------------------------------------------------------------------------

class TestProdAccountsHighRisk:

    def test_prod_account_yields_high_risk(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[_prod_account()],
        )
        assert result["risk_level"] == "high"
        assert result["action_required"] is True

    def test_prod_account_risk_score_higher_than_non_prod(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[_prod_account("111111111111"), _non_prod_account("222222222222")],
        )
        scores = {
            a["account_id"]: a["risk_score"] for a in result["affected_accounts"]
        }
        assert scores["111111111111"] > scores["222222222222"]


# ---------------------------------------------------------------------------
# Req 9.2 – BREAKING_CHANGE with only non-prod accounts: lower risk
# ---------------------------------------------------------------------------

class TestNonProdAccountsLowerRisk:

    def test_single_non_prod_yields_low_risk(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[_non_prod_account()],
        )
        assert result["risk_level"] == "low"
        assert result["action_required"] is True

    def test_multiple_non_prod_yields_medium_risk(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[
                _non_prod_account("222222222222"),
                _non_prod_account("333333333333"),
            ],
        )
        assert result["risk_level"] == "medium"


# ---------------------------------------------------------------------------
# Req 9.4 – No affected resources: action_required false
# ---------------------------------------------------------------------------

class TestNoAffectedResources:

    def test_empty_accounts_no_action_required(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[],
        )
        assert result["action_required"] is False
        assert "no action required" in result["summary"].lower()

    def test_empty_accounts_risk_level_low(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[],
        )
        assert result["risk_level"] == "low"


# ---------------------------------------------------------------------------
# Req 9.1, 9.2, 9.3 – Mixed environments
# ---------------------------------------------------------------------------

class TestMixedEnvironments:

    def test_mixed_env_high_risk_due_to_prod(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[_prod_account("111111111111"), _non_prod_account("222222222222")],
        )
        assert result["risk_level"] == "high"

    def test_all_accounts_listed_in_summary(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[_prod_account("111111111111"), _non_prod_account("222222222222")],
        )
        account_ids = {a["account_id"] for a in result["affected_accounts"]}
        assert account_ids == {"111111111111", "222222222222"}

    def test_each_account_has_required_action(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[_non_prod_account("111111111111"), _non_prod_account("222222222222")],
        )
        for acct in result["affected_accounts"]:
            assert "required_action" in acct
            assert len(acct["required_action"]) > 0

    def test_summary_includes_service(self):
        result = _analyze(
            notification=_notif(service="CASSANDRA"),
            affected_accounts=[_non_prod_account()],
        )
        assert "CASSANDRA" in result["summary"]

    def test_notification_id_propagated(self):
        result = _analyze(
            notification=_notif(arn="arn:aws:health:us-east-1::event/EKS/456"),
            affected_accounts=[_non_prod_account()],
        )
        assert result["notification_id"] == "arn:aws:health:us-east-1::event/EKS/456"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_accounts_as_dicts_with_resources(self):
        result = _analyze(
            notification=_notif(),
            affected_accounts=[
                _prod_account("111111111111", resources=["arn:resource:1"]),
            ],
        )
        assert result["affected_accounts"][0]["affected_resources"] == ["arn:resource:1"]
        assert result["action_required"] is True

    def test_accounts_as_plain_strings_fallback(self):
        """Plain string account IDs still work but get environment_type 'unknown'."""
        result = _analyze(
            notification=_notif(),
            affected_accounts=["111111111111"],
        )
        assert result["affected_accounts"][0]["account_id"] == "111111111111"
        assert result["affected_accounts"][0]["environment_type"] == "unknown"
        assert result["affected_accounts"][0]["affected_resources"] == []

    def test_unknown_environment_type_gets_low_risk_score(self):
        """Accounts with unknown environment_type get non-production risk score."""
        result = _analyze(
            notification=_notif(),
            affected_accounts=[{"account_id": "111111111111", "environment_type": "unknown"}],
        )
        assert result["affected_accounts"][0]["risk_score"] == 1
