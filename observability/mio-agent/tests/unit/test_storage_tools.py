"""Unit tests for storage tools using moto AWS mocking."""

from __future__ import annotations

import json
from datetime import datetime

import boto3
import pytest
from moto import mock_aws

from mio_agent.models.assessment import (
    AccessTier,
    ConfidenceLevel,
    DimensionScore,
    OMS,
    RiskLevel,
)
from mio_agent.tools.storage_tools import (
    ASSESSMENTS_TABLE,
    ACCOUNTS_TABLE,
    REPORTS_BUCKET,
    get_assessment_history,
    get_accounts_list,
    store_assessment,
    store_report,
    get_report_url,
)

ACCOUNT_ID = "123456789012"
REGION = "us-east-1"


def _setup_aws():
    """Create required AWS resources inside a mock_aws context."""
    dynamodb = boto3.client("dynamodb", region_name=REGION)
    dynamodb.create_table(
        TableName=ASSESSMENTS_TABLE,
        KeySchema=[
            {"AttributeName": "account_id", "KeyType": "HASH"},
            {"AttributeName": "assessment_timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "account_id", "AttributeType": "S"},
            {"AttributeName": "assessment_timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    dynamodb.create_table(
        TableName=ACCOUNTS_TABLE,
        KeySchema=[{"AttributeName": "account_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "account_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=REPORTS_BUCKET)
    return dynamodb, s3


def make_oms() -> OMS:
    dims = {
        "metrics_coverage": DimensionScore(score=3.0, weight=0.25),
        "alerting_quality": DimensionScore(score=2.5, weight=0.25),
        "log_intelligence": DimensionScore(score=3.0, weight=0.20),
        "distributed_tracing": DimensionScore(score=2.0, weight=0.20),
        "incident_readiness": DimensionScore(score=3.0, weight=0.10),
    }
    return OMS(
        assessment_id="test-assessment-001",
        account_id=ACCOUNT_ID,
        account_name="Test Account",
        overall_oms=2.8,
        dimensions=dims,
        risk_level=RiskLevel.HIGH,
        access_tier_used=AccessTier.TIER3,
        confidence=ConfidenceLevel.HIGH,
        total_findings=3,
    )





class TestStoreAssessment:
    @mock_aws
    def test_stores_and_retrieves_assessment(self):
        _setup_aws()
        oms = make_oms()
        assessment_id = store_assessment(oms, json.dumps([]), region=REGION)
        assert assessment_id == oms.assessment_id

    @mock_aws
    def test_history_retrievable_after_store(self):
        _setup_aws()
        oms = make_oms()
        store_assessment(oms, json.dumps([]), region=REGION)
        history = get_assessment_history(ACCOUNT_ID, region=REGION)
        assert len(history) == 1
        assert history[0]["overall_oms"] == 2.8
        assert history[0]["risk_level"] == "HIGH"

    @mock_aws
    def test_multiple_assessments_stored(self):
        _setup_aws()
        oms1 = make_oms()
        oms1.assessment_id = "test-001"
        oms2 = make_oms()
        oms2.assessment_id = "test-002"
        oms2.assessment_timestamp = datetime(2025, 1, 15)

        store_assessment(oms1, "[]", region=REGION)
        store_assessment(oms2, "[]", region=REGION)
        history = get_assessment_history(ACCOUNT_ID, region=REGION)
        assert len(history) == 2

    @mock_aws
    def test_empty_history_returns_empty_list(self):
        _setup_aws()
        history = get_assessment_history("999999999999", region=REGION)
        assert history == []


class TestGetAccountsList:
    @mock_aws
    def test_returns_configured_accounts(self):
        ddb, _ = _setup_aws()
        ddb.put_item(
            TableName=ACCOUNTS_TABLE,
            Item={
                "account_id": {"S": ACCOUNT_ID},
                "account_name": {"S": "Test Account"},
                "access_tier": {"S": "tier3"},
                "tam_alias": {"S": "test-tam"},
                "enabled": {"BOOL": True},
            },
        )
        accounts = get_accounts_list(region=REGION)
        assert len(accounts) == 1
        assert accounts[0]["account_id"] == ACCOUNT_ID

    @mock_aws
    def test_empty_accounts_table(self):
        _setup_aws()
        accounts = get_accounts_list(region=REGION)
        assert accounts == []


class TestStoreReport:
    @mock_aws
    def test_stores_report_to_s3(self):
        _setup_aws()
        key = store_report(
            account_id=ACCOUNT_ID,
            assessment_id="test-001",
            report_content="# Test Report",
            report_type="tam_brief",
            region=REGION,
        )
        assert ACCOUNT_ID in key
        assert "tam_brief" in key
        assert key.endswith(".md")

    @mock_aws
    def test_get_report_url_returns_presigned_url(self):
        _setup_aws()
        key = store_report(
            account_id=ACCOUNT_ID,
            assessment_id="test-001",
            report_content="Test",
            report_type="tam_brief",
            region=REGION,
        )
        url = get_report_url(key, expiry_seconds=3600, region=REGION)
        assert url.startswith("https://")
        assert REPORTS_BUCKET in url
