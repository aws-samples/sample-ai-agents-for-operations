# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for check_training_viability tool."""

import json
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))

from tools.check_training_viability import (
    _check_schema_completeness,
    _check_minimum_size,
    _extract_model_size,
    check_training_viability,
)
import polars as pl


@pytest.fixture
def instruction_response_jsonl(tmp_path):
    records = [
        {"instruction": f"Detailed question number {i} about AWS services and cloud architecture",
         "response": f"A comprehensive answer about cloud services and AWS for question {i}."}
        for i in range(600)
    ]
    p = tmp_path / "train.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


@pytest.fixture
def tiny_dataset_jsonl(tmp_path):
    records = [{"instruction": f"Q{i}", "response": f"A{i}"} for i in range(5)]
    p = tmp_path / "tiny.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


class TestExtractModelSize:
    def test_70b_extraction(self):
        assert _extract_model_size("meta.llama3-70b-instruct-v1:0") == "70b"

    def test_8b_extraction(self):
        assert _extract_model_size("meta.llama3-8b-instruct-v1:0") == "8b"

    def test_unknown_model_default(self):
        assert _extract_model_size("some.unknown.model") == "default"

    def test_claude_default(self):
        result = _extract_model_size("anthropic.claude-sonnet-4-20250514-v1:0")
        assert result == "default"


class TestCheckSchemaCompleteness:
    def test_instruction_response_matched(self):
        df = pl.DataFrame({"instruction": ["q1"], "response": ["a1"]})
        result = _check_schema_completeness(df)
        assert result["schema_valid"] is True
        assert "instruction_response" in result["matched_patterns"]

    def test_prompt_completion_matched(self):
        df = pl.DataFrame({"prompt": ["p1"], "completion": ["c1"]})
        result = _check_schema_completeness(df)
        assert "prompt_completion" in result["matched_patterns"]

    def test_unknown_schema_invalid(self):
        df = pl.DataFrame({"col_a": ["x"], "col_b": ["y"]})
        result = _check_schema_completeness(df)
        assert result["schema_valid"] is False

    def test_case_insensitive_matching(self):
        df = pl.DataFrame({"INSTRUCTION": ["q"], "RESPONSE": ["a"]})
        result = _check_schema_completeness(df)
        assert result["schema_valid"] is True


class TestCheckMinimumSize:
    def test_sufficient_size(self):
        df = pl.DataFrame({"instruction": ["q"] * 600, "response": ["a"] * 600})
        result = _check_minimum_size(df, "meta.llama3-8b-instruct-v1:0")
        assert result["meets_minimum"] is True

    def test_insufficient_size(self):
        df = pl.DataFrame({"instruction": ["q"] * 10, "response": ["a"] * 10})
        result = _check_minimum_size(df, "meta.llama3-70b-instruct-v1:0")
        assert result["meets_minimum"] is False
        assert result["shortfall"] > 0


class TestCheckTrainingViability:
    def test_good_dataset_viable(self, instruction_response_jsonl):
        result = check_training_viability(
            instruction_response_jsonl,
            target_model="meta.llama3-8b-instruct-v1:0",
        )
        assert "overall_viable" in result
        assert "blocking_issues" in result
        assert isinstance(result["blocking_issues"], list)

    def test_tiny_dataset_warns(self, tiny_dataset_jsonl):
        result = check_training_viability(
            tiny_dataset_jsonl,
            target_model="meta.llama3-70b-instruct-v1:0",
        )
        # Should have size warning
        assert len(result["warnings"]) > 0 or not result["size_check"]["meets_minimum"]

    def test_required_keys_present(self, instruction_response_jsonl):
        result = check_training_viability(instruction_response_jsonl)
        required = ["token_budget", "schema_check", "size_check", "leakage_check",
                    "overall_viable", "blocking_issues", "warnings", "duration_ms"]
        for key in required:
            assert key in result, f"Missing key: {key}"
