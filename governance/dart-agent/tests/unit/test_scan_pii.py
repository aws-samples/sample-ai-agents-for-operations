# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for scan_pii tool — mocks Amazon Comprehend."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))

from tools.scan_pii import (
    _get_text_columns,
    _redact_text,
)


@pytest.fixture
def sample_jsonl_with_pii(tmp_path):
    records = [
        {"instruction": "My email is john.doe@example.com", "response": "Contact me at 555-123-4567"},
        {"instruction": "What is cloud computing?", "response": "Cloud computing is the delivery of services."},
        {"instruction": "Call me at +44 7700 900000", "response": "Sure, I will call you."},
    ]
    p = tmp_path / "pii_data.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


@pytest.fixture
def clean_jsonl(tmp_path):
    records = [
        {"instruction": "What is AWS Lambda?", "response": "Lambda is a serverless compute service."},
        {"instruction": "What is Amazon S3?", "response": "S3 is an object storage service."},
    ]
    p = tmp_path / "clean.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


class TestGetTextColumns:
    def test_identifies_string_columns(self):
        df = pl.DataFrame({"text": ["hello"], "number": [42], "flag": [True]})
        cols = _get_text_columns(df)
        assert "text" in cols
        assert "number" not in cols
        assert "flag" not in cols


class TestRedactText:
    def test_mask_strategy(self):
        text = "My email is john@example.com"
        entities = [{"BeginOffset": 12, "EndOffset": 28, "Type": "EMAIL"}]
        result = _redact_text(text, entities, "mask")
        assert "john@example.com" not in result
        assert "[REDACTED_EMAIL]" in result

    def test_remove_strategy_returns_empty(self):
        text = "My phone is 555-1234"
        entities = [{"BeginOffset": 12, "EndOffset": 20, "Type": "PHONE"}]
        result = _redact_text(text, entities, "remove")
        assert result == ""

    def test_no_entities_unchanged(self):
        text = "Clean text with no PII here"
        result = _redact_text(text, [], "mask")
        assert result == text

    def test_multiple_entities_all_masked(self):
        text = "Email: a@b.com, Phone: 555-0000"
        entities = [
            {"BeginOffset": 7, "EndOffset": 14, "Type": "EMAIL"},
            {"BeginOffset": 23, "EndOffset": 31, "Type": "PHONE"},
        ]
        result = _redact_text(text, entities, "mask")
        assert "a@b.com" not in result
        assert "555-0000" not in result


class TestScanPiiWithMock:
    """Tests that mock Amazon Comprehend to avoid real API calls."""

    def _make_comprehend_response(self, entities):
        """Build a mock Comprehend response."""
        return {"Entities": entities}

    @patch("tools.scan_pii.boto3.client")
    def test_pii_detected(self, mock_boto3_client, sample_jsonl_with_pii):
        """Mock Comprehend's BatchDetectPiiEntities to return PII entities.

        scan_pii calls batch_detect_pii_entities(TextList=...) once per text
        column (here: 'instruction' and 'response'), each batch holding the 3
        records. The response uses a ResultList whose items carry their batch
        position via the 'Index' field.
        """
        mock_comprehend = MagicMock()
        mock_boto3_client.return_value = mock_comprehend

        def batch_side_effect(TextList, LanguageCode="en", **kwargs):
            # Detect an EMAIL in any document that contains '@', and a PHONE in
            # any document with a digit run — mirrors what Comprehend would find
            # in the sample fixture without depending on column ordering.
            result_list = []
            for i, doc in enumerate(TextList):
                entities = []
                if "@" in doc:
                    entities.append({"BeginOffset": 0, "EndOffset": 1, "Type": "EMAIL"})
                if any(ch.isdigit() for ch in doc):
                    entities.append({"BeginOffset": 0, "EndOffset": 1, "Type": "PHONE"})
                if entities:
                    result_list.append({"Index": i, "Entities": entities})
            return {"ResultList": result_list, "ErrorList": []}

        mock_comprehend.batch_detect_pii_entities.side_effect = batch_side_effect

        from tools.scan_pii import scan_pii
        result = scan_pii(sample_jsonl_with_pii)
        assert result["pii_found"] is True
        assert result["affected_record_count"] > 0

    @patch("tools.scan_pii.boto3.client")
    def test_no_pii_clean_dataset(self, mock_boto3_client, clean_jsonl):
        """Mock Comprehend to return no PII."""
        mock_comprehend = MagicMock()
        mock_boto3_client.return_value = mock_comprehend
        mock_comprehend.batch_detect_pii_entities.return_value = {
            "ResultList": [], "ErrorList": []
        }

        from tools.scan_pii import scan_pii
        result = scan_pii(clean_jsonl)
        assert result["pii_found"] is False
        assert result["affected_record_count"] == 0

    @patch("tools.scan_pii.boto3.client")
    def test_result_has_required_keys(self, mock_boto3_client, clean_jsonl):
        mock_comprehend = MagicMock()
        mock_boto3_client.return_value = mock_comprehend
        mock_comprehend.batch_detect_pii_entities.return_value = {
            "ResultList": [], "ErrorList": []
        }

        from tools.scan_pii import scan_pii
        result = scan_pii(clean_jsonl)
        required = [
            "pii_found", "total_records", "affected_record_count",
            "entity_type_counts", "duration_ms",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"
