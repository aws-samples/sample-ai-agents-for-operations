# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for apply_safe_fixes tool."""

import json
from pathlib import Path

import polars as pl
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))

from tools.apply_safe_fixes import (
    _record_hash,
    _fix_encoding,
    apply_safe_fixes,
)


@pytest.fixture
def duplicate_jsonl(tmp_path):
    """JSONL with exact duplicates and empty records."""
    records = [
        {"instruction": "What is AWS?", "response": "Amazon Web Services."},
        {"instruction": "What is AWS?", "response": "Amazon Web Services."},   # exact dup
        {"instruction": "What is S3?", "response": "Object storage on AWS."},
        {"instruction": "Empty test", "response": ""},                          # empty
        {"instruction": "Short", "response": "Hi"},                             # near-empty
        {"instruction": "Unique question", "response": "A unique and sufficiently long answer."},
    ]
    p = tmp_path / "input.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


@pytest.fixture
def clean_jsonl(tmp_path):
    """JSONL with no issues."""
    records = [
        {"instruction": f"Good question {i}", "response": f"A sufficiently long answer for question {i} with detail."}
        for i in range(5)
    ]
    p = tmp_path / "clean.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


class TestRecordHash:
    def test_same_record_same_hash(self):
        r = {"a": "hello", "b": "world"}
        assert _record_hash(r) == _record_hash(r)

    def test_different_records_different_hash(self):
        r1 = {"a": "hello", "b": "world"}
        r2 = {"a": "hello", "b": "earth"}
        assert _record_hash(r1) != _record_hash(r2)

    def test_order_independent(self):
        r1 = {"a": "hello", "b": "world"}
        r2 = {"b": "world", "a": "hello"}
        assert _record_hash(r1) == _record_hash(r2)


class TestFixEncoding:
    def test_valid_utf8_unchanged(self):
        text = "Hello, world! This is valid UTF-8."
        assert _fix_encoding(text) == text

    def test_unicode_preserved(self):
        text = "café résumé naïve"
        result = _fix_encoding(text)
        assert "caf" in result

    def test_replacement_char_for_invalid(self):
        # Simulate a string with replacement character
        text = "hello \ufffd world"
        result = _fix_encoding(text)
        assert "hello" in result
        assert "world" in result


class TestApplySafeFixes:
    def test_removes_exact_duplicates(self, duplicate_jsonl, tmp_path):
        output = str(tmp_path / "output.jsonl")
        result = apply_safe_fixes(duplicate_jsonl, output_path=output)
        assert result["exact_duplicates_removed"] == 1
        assert result["records_after"] < result["records_before"]

    def test_removes_empty_records(self, duplicate_jsonl, tmp_path):
        output = str(tmp_path / "output.jsonl")
        result = apply_safe_fixes(duplicate_jsonl, output_path=output)
        assert result["empty_records_removed"] >= 1

    def test_output_file_created(self, duplicate_jsonl, tmp_path):
        output = str(tmp_path / "output.jsonl")
        apply_safe_fixes(duplicate_jsonl, output_path=output)
        assert Path(output).exists()

    def test_original_file_unchanged(self, duplicate_jsonl):
        original_content = Path(duplicate_jsonl).read_text()
        apply_safe_fixes(duplicate_jsonl)
        assert Path(duplicate_jsonl).read_text() == original_content

    def test_clean_dataset_unchanged(self, clean_jsonl, tmp_path):
        output = str(tmp_path / "output.jsonl")
        result = apply_safe_fixes(clean_jsonl, output_path=output)
        assert result["exact_duplicates_removed"] == 0
        assert result["records_before"] == result["records_after"]

    def test_result_has_required_keys(self, duplicate_jsonl, tmp_path):
        output = str(tmp_path / "output.jsonl")
        result = apply_safe_fixes(duplicate_jsonl, output_path=output)
        required = [
            "records_before", "records_after", "exact_duplicates_removed",
            "empty_records_removed", "output_path", "duration_ms",
        ]
        for key in required:
            assert key in result, f"Missing key: {key}"

    def test_auto_output_path_generated(self, clean_jsonl):
        result = apply_safe_fixes(clean_jsonl)
        assert result["output_path"] is not None
        assert "_fixed" in result["output_path"]
