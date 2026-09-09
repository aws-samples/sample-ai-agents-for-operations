# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Integration tests for DART Agent end-to-end flow.

These tests mock Amazon Bedrock and Amazon Comprehend to avoid real API calls,
but exercise the full tool chain against real local dataset files.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))


@pytest.fixture
def sample_dataset(tmp_path):
    """Create a realistic sample JSONL dataset for end-to-end testing."""
    records = []
    # Good records
    for i in range(40):
        records.append({
            "instruction": f"Explain AWS service number {i} in detail for a cloud architect.",
            "response": f"AWS service {i} provides scalable cloud infrastructure. "
                        f"It integrates with other AWS services through standard APIs. "
                        f"Best practice is to use least-privilege IAM roles."
        })
    # Exact duplicates (2)
    records.append({"instruction": "What is Amazon S3?", "response": "Amazon S3 is object storage."})
    records.append({"instruction": "What is Amazon S3?", "response": "Amazon S3 is object storage."})
    # Empty response (1)
    records.append({"instruction": "Empty question", "response": ""})
    # PII record (1)
    records.append({"instruction": "My email is test@example.com", "response": "Got it."})

    p = tmp_path / "train.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


class TestToolChainIntegration:
    """Tests that run the full tool chain against a real dataset."""

    def test_profile_then_deduplicate(self, sample_dataset):
        """Profile a dataset then run deduplication — results should be consistent."""
        from tools.profile_dataset import profile_dataset
        from tools.detect_duplicates import detect_duplicates

        profile = profile_dataset(sample_dataset)
        dupes = detect_duplicates(sample_dataset)

        assert profile["total_records"] == dupes["total_records"]
        assert dupes["exact_duplicate_count"] >= 1  # we added 1 exact dup

    def test_profile_detects_empty_records(self, sample_dataset):
        """Profile should flag the empty response record."""
        from tools.profile_dataset import profile_dataset
        profile = profile_dataset(sample_dataset)
        assert profile["empty_record_count"] >= 1

    def test_apply_fixes_reduces_records(self, sample_dataset, tmp_path):
        """apply_safe_fixes should remove duplicates and empties."""
        from tools.apply_safe_fixes import apply_safe_fixes
        output = str(tmp_path / "fixed.jsonl")
        result = apply_safe_fixes(sample_dataset, output_path=output)
        assert result["records_after"] < result["records_before"]
        assert result["exact_duplicates_removed"] >= 1
        assert Path(output).exists()

    def test_cost_estimate_shows_savings_after_fixes(self, sample_dataset):
        """Cost estimate with duplicate_rate > 0 should show savings."""
        from tools.estimate_training_cost import estimate_training_cost
        result = estimate_training_cost(
            total_tokens=5_000_000,
            duplicate_rate=0.10,
            empty_record_rate=0.02,
        )
        assert result["savings_usd"] > 0
        assert result["cost_after_fixes"]["estimated_cost_usd"] < \
               result["cost_before_fixes"]["estimated_cost_usd"]

    def test_viability_check_recognises_schema(self, sample_dataset):
        """check_training_viability should recognise instruction_response schema."""
        from tools.check_training_viability import check_training_viability
        result = check_training_viability(sample_dataset)
        assert result["schema_check"]["schema_valid"] is True
        assert "instruction_response" in result["schema_check"]["matched_patterns"]

    @patch("tools.scan_pii.boto3.client")
    def test_pii_scan_with_mock_comprehend(self, mock_boto3_client, sample_dataset):
        """scan_pii should integrate with mocked Amazon Comprehend."""
        mock_comprehend = MagicMock()
        mock_boto3_client.return_value = mock_comprehend

        # Return PII for one record, clean for others
        def side_effect(Text, LanguageCode):
            if "test@example.com" in Text:
                return {"Entities": [{"BeginOffset": 12, "EndOffset": 28, "Type": "EMAIL"}]}
            return {"Entities": []}

        mock_comprehend.detect_pii_entities.side_effect = side_effect

        from tools.scan_pii import scan_pii
        result = scan_pii(sample_dataset)
        assert result["pii_found"] is True
        assert result["affected_record_count"] >= 1

    def test_full_report_synthesis(self, sample_dataset, tmp_path):
        """generate_preflight_report should synthesise findings from all tools."""
        from tools.profile_dataset import profile_dataset
        from tools.detect_duplicates import detect_duplicates
        from tools.estimate_training_cost import estimate_training_cost
        from tools.check_training_viability import check_training_viability
        from tools.generate_preflight_report import generate_preflight_report

        profile = profile_dataset(sample_dataset)
        dupes = detect_duplicates(sample_dataset)
        viability = check_training_viability(sample_dataset)
        cost = estimate_training_cost(
            total_tokens=profile["text_stats"].get("instruction", {}).get("token_total", 1_000_000),
            duplicate_rate=dupes["duplicate_rate"],
        )
        pii_mock = {"pii_found": False, "affected_record_count": 0, "entity_type_counts": {}}

        with patch("tools.generate_preflight_report.boto3.client") as mock_bedrock_client:
            mock_runtime = MagicMock()
            mock_bedrock_client.return_value = mock_runtime
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps(
                {"content": [{"text": "⚠️ GO WITH FIXES — 1 exact duplicate found. Remove and retrain."}]}
            ).encode()
            mock_runtime.invoke_model.return_value = {"body": mock_body}

            report = generate_preflight_report(
                dataset_path=sample_dataset,
                target_model="meta.llama3-8b-instruct-v1:0",
                profile_result=profile,
                duplicates_result=dupes,
                pii_result=pii_mock,
                viability_result=viability,
                cost_result=cost,
            )

        assert "recommendation" in report
        assert report["recommendation"] in ("GO", "GO WITH FIXES", "NO-GO")
        assert "quality_score" in report
        assert 0 <= report["quality_score"] <= 100
        assert "findings" in report
        assert "narrative" in report
