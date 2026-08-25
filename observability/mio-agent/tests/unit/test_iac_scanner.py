"""Unit tests for IaC Scanner tools and agent."""

from __future__ import annotations

import json

import pytest

from mio_agent.tools.iac_tools import (
    analyze_iac_repository,
    identify_monitoring_gaps_in_iac,
)


LAMBDA_WITHOUT_TRACING = json.dumps({
    "AWSTemplateFormatVersion": "2010-09-09",
    "Resources": {
        "OrderProcessor": {
            "Type": "AWS::Lambda::Function",
            "Properties": {
                "FunctionName": "order-processor",
                "Runtime": "python3.12",
                "Handler": "index.handler",
                "Role": {"Fn::GetAtt": ["LambdaRole", "Arn"]},
                "Code": {"S3Bucket": "my-bucket", "S3Key": "code.zip"},
            },
        }
    },
})

LAMBDA_WITH_TRACING = json.dumps({
    "AWSTemplateFormatVersion": "2010-09-09",
    "Resources": {
        "OrderProcessor": {
            "Type": "AWS::Lambda::Function",
            "Properties": {
                "FunctionName": "order-processor",
                "Runtime": "python3.12",
                "Handler": "index.handler",
                "Role": {"Fn::GetAtt": ["LambdaRole", "Arn"]},
                "Code": {"S3Bucket": "my-bucket", "S3Key": "code.zip"},
                "TracingConfig": {"Mode": "Active"},
            },
        }
    },
})

RDS_WITHOUT_MONITORING = json.dumps({
    "Resources": {
        "ProdDatabase": {
            "Type": "AWS::RDS::DBInstance",
            "Properties": {
                "DBInstanceClass": "db.t3.medium",
                "Engine": "postgres",
                "MasterUsername": "admin",
                "MasterUserPassword": "{{resolve:secretsmanager:db-secret}}",
            },
        }
    }
})

API_GW_WITHOUT_TRACING = json.dumps({
    "Resources": {
        "ProdStage": {
            "Type": "AWS::ApiGateway::Stage",
            "Properties": {
                "RestApiId": {"Ref": "MyApi"},
                "StageName": "prod",
            },
        }
    }
})

MIXED_TEMPLATE = json.dumps({
    "Resources": {
        "TracedLambda": {
            "Type": "AWS::Lambda::Function",
            "Properties": {
                "FunctionName": "traced-fn",
                "TracingConfig": {"Mode": "Active"},
            },
        },
        "UntracedLambda": {
            "Type": "AWS::Lambda::Function",
            "Properties": {
                "FunctionName": "untraced-fn",
            },
        },
    }
})


class TestAnalyzeIacRepository:
    def test_parses_json_cloudformation(self):
        result = analyze_iac_repository(LAMBDA_WITHOUT_TRACING, "json")
        assert result["template_type"] == "CloudFormation"
        assert result["resource_count"] == 1

    def test_detects_cloudformation_type(self):
        result = analyze_iac_repository(LAMBDA_WITH_TRACING, "json")
        assert "CloudFormation" in result["template_type"]

    def test_auto_detects_json(self):
        result = analyze_iac_repository(LAMBDA_WITHOUT_TRACING)
        assert result["resource_count"] == 1

    def test_returns_resource_list(self):
        result = analyze_iac_repository(LAMBDA_WITHOUT_TRACING, "json")
        resources = result["resources"]
        assert len(resources) == 1
        assert resources[0]["type"] == "AWS::Lambda::Function"

    def test_monitoring_coverage_lambda_no_tracing(self):
        result = analyze_iac_repository(LAMBDA_WITHOUT_TRACING, "json")
        coverage = result["monitoring_coverage"]
        assert "OrderProcessor" in coverage
        assert coverage["OrderProcessor"]["xray_tracing"] is False

    def test_monitoring_coverage_lambda_with_tracing(self):
        result = analyze_iac_repository(LAMBDA_WITH_TRACING, "json")
        coverage = result["monitoring_coverage"]
        assert coverage["OrderProcessor"]["xray_tracing"] is True

    def test_handles_invalid_json(self):
        result = analyze_iac_repository("not valid json {{", "json")
        assert "error" in result

    def test_rds_without_monitoring(self):
        result = analyze_iac_repository(RDS_WITHOUT_MONITORING, "json")
        coverage = result["monitoring_coverage"]
        assert "ProdDatabase" in coverage
        assert coverage["ProdDatabase"]["enhanced_monitoring"] is False

    def test_api_gateway_without_tracing(self):
        result = analyze_iac_repository(API_GW_WITHOUT_TRACING, "json")
        coverage = result["monitoring_coverage"]
        assert "ProdStage" in coverage
        assert coverage["ProdStage"]["xray_tracing"] is False


class TestIdentifyMonitoringGaps:
    def test_lambda_without_tracing_produces_finding(self):
        gaps = identify_monitoring_gaps_in_iac(LAMBDA_WITHOUT_TRACING, "json")
        assert len(gaps) >= 1
        gap_types = [g["gap"] for g in gaps]
        assert any("tracing" in g.lower() for g in gap_types)

    def test_lambda_with_tracing_no_tracing_finding(self):
        gaps = identify_monitoring_gaps_in_iac(LAMBDA_WITH_TRACING, "json")
        tracing_gaps = [g for g in gaps if "tracing" in g["gap"].lower()]
        assert len(tracing_gaps) == 0

    def test_rds_without_monitoring_produces_findings(self):
        gaps = identify_monitoring_gaps_in_iac(RDS_WITHOUT_MONITORING, "json")
        assert len(gaps) >= 1
        gap_types = [g["gap"] for g in gaps]
        assert any("enhanced monitoring" in g.lower() for g in gap_types)

    def test_mixed_template_only_flags_untraced(self):
        gaps = identify_monitoring_gaps_in_iac(MIXED_TEMPLATE, "json")
        resource_ids = [g["resource_id"] for g in gaps]
        assert "UntracedLambda" in resource_ids
        assert "TracedLambda" not in resource_ids

    def test_api_gw_without_tracing_produces_finding(self):
        gaps = identify_monitoring_gaps_in_iac(API_GW_WITHOUT_TRACING, "json")
        assert len(gaps) >= 1
        gap_types = [g["gap"] for g in gaps]
        assert any("tracing" in g.lower() for g in gap_types)

    def test_gap_has_recommendation(self):
        gaps = identify_monitoring_gaps_in_iac(LAMBDA_WITHOUT_TRACING, "json")
        for gap in gaps:
            assert "recommendation" in gap
            assert len(gap["recommendation"]) > 10

    def test_gap_has_severity(self):
        gaps = identify_monitoring_gaps_in_iac(LAMBDA_WITHOUT_TRACING, "json")
        for gap in gaps:
            assert gap.get("severity") in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
