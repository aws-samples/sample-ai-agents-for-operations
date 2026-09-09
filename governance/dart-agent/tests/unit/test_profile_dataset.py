# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for profile_dataset tool."""

import json
import tempfile
from pathlib import Path

import polars as pl
import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))

from tools.profile_dataset import (
    _detect_format,
    _detect_encoding,
    _get_tokenizer,
    _load_dataset,
    _compute_text_stats,
)


@pytest.fixture
def sample_jsonl(tmp_path):
    """Create a sample JSONL file for testing."""
    records = [
        {"instruction": "What is AWS?", "response": "Amazon Web Services is a cloud platform."},
        {"instruction": "What is S3?", "response": "Amazon S3 is an object storage service."},
        {"instruction": "What is EC2?", "response": "Amazon EC2 provides virtual compute instances."},
        {"instruction": "Empty response", "response": ""},
        {"instruction": "Short", "response": "Hi"},
    ]
    p = tmp_path / "test.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV file."""
    p = tmp_path / "test.csv"
    df = pl.DataFrame({
        "prompt": ["What is Lambda?", "What is DynamoDB?"],
        "completion": ["Lambda is serverless compute.", "DynamoDB is a NoSQL database."],
    })
    df.write_csv(p)
    return str(p)


class TestDetectFormat:
    def test_jsonl_extension(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a": 1}\n')
        assert _detect_format(p) == "jsonl"

    def test_csv_extension(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("a,b\n1,2\n")
        assert _detect_format(p) == "csv"

    def test_parquet_extension(self, tmp_path):
        p = tmp_path / "data.parquet"
        df = pl.DataFrame({"a": [1, 2]})
        df.write_parquet(p)
        assert _detect_format(p) == "parquet"

    def test_json_content_sniff(self, tmp_path):
        p = tmp_path / "data.unknown"
        p.write_text('[{"a": 1}]')
        assert _detect_format(p) == "json"


class TestDetectEncoding:
    def test_utf8_file(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_bytes("Hello world".encode("utf-8"))
        encoding = _detect_encoding(p)
        assert encoding.lower() in ("utf-8", "ascii")

    def test_latin1_file(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_bytes("café résumé".encode("latin-1"))
        encoding = _detect_encoding(p)
        # chardet should detect non-UTF-8
        assert encoding is not None


class TestLoadDataset:
    def test_load_jsonl(self, tmp_path):
        p = tmp_path / "data.jsonl"
        p.write_text('{"a": "hello"}\n{"a": "world"}\n')
        df = _load_dataset(p, "jsonl")
        assert len(df) == 2
        assert "a" in df.columns

    def test_load_csv(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("col1,col2\nfoo,bar\nbaz,qux\n")
        df = _load_dataset(p, "csv")
        assert len(df) == 2
        assert set(df.columns) == {"col1", "col2"}

    def test_unsupported_format_raises(self, tmp_path):
        p = tmp_path / "data.xyz"
        p.write_text("test")
        with pytest.raises(ValueError, match="Unsupported format"):
            _load_dataset(p, "xyz")


class TestComputeTextStats:
    def test_basic_stats(self):
        enc = _get_tokenizer("anthropic.claude-sonnet-4-20250514-v1:0")
        series = pl.Series(["hello world", "this is a longer text for testing purposes"])
        stats = _compute_text_stats(series, enc)
        assert "char_min" in stats
        assert "token_total" in stats
        assert stats["char_min"] > 0
        assert stats["token_total"] > 0

    def test_empty_series_handled(self):
        enc = _get_tokenizer("anthropic.claude-sonnet-4-20250514-v1:0")
        series = pl.Series(["", None, "   "])
        # Should not raise
        stats = _compute_text_stats(series.drop_nulls().filter(pl.Series([True, False])), enc)
        assert stats is not None


class TestProfileDatasetIntegration:
    def test_profile_jsonl(self, sample_jsonl):
        """Integration test — profile a real JSONL file."""
        from tools.profile_dataset import profile_dataset
        result = profile_dataset(sample_jsonl)
        assert result["format"] == "jsonl"
        assert result["total_records"] == 5
        assert "instruction" in result["schema"]
        assert "response" in result["schema"]
        assert result["empty_record_count"] >= 1  # empty response record
        assert result["detected_schema_pattern"] == "instruction_response"

    def test_profile_csv(self, sample_csv):
        """Integration test — profile a CSV file."""
        from tools.profile_dataset import profile_dataset
        result = profile_dataset(sample_csv)
        assert result["format"] == "csv"
        assert result["total_records"] == 2
        assert result["detected_schema_pattern"] == "prompt_completion"
