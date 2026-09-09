# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for detect_duplicates tool."""

import json
import tempfile
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))

from tools.detect_duplicates import (
    _record_to_text,
    _text_to_shingles,
    _compute_minhash,
    detect_duplicates,
)


@pytest.fixture
def clean_jsonl(tmp_path):
    """JSONL with no duplicates."""
    records = [
        {"instruction": f"Question number {i}", "response": f"Answer number {i} with some text"}
        for i in range(20)
    ]
    p = tmp_path / "clean.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


@pytest.fixture
def duplicate_jsonl(tmp_path):
    """JSONL with 5 exact duplicates."""
    records = [
        {"instruction": "What is AWS?", "response": "Amazon Web Services."},
        {"instruction": "What is AWS?", "response": "Amazon Web Services."},  # exact dup
        {"instruction": "What is S3?", "response": "Object storage."},
        {"instruction": "What is S3?", "response": "Object storage."},         # exact dup
        {"instruction": "What is EC2?", "response": "Virtual machines."},
        {"instruction": "What is EC2?", "response": "Virtual machines."},       # exact dup
        {"instruction": "Unique question A", "response": "Unique answer A here."},
        {"instruction": "Unique question B", "response": "Unique answer B here."},
    ]
    p = tmp_path / "dupes.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


@pytest.fixture
def near_duplicate_jsonl(tmp_path):
    """JSONL with near-duplicates (slight variations)."""
    records = [
        {"instruction": "What is Amazon S3?", "response": "S3 is an object storage service on AWS."},
        {"instruction": "What is Amazon S3?", "response": "S3 is an object storage service on AWS!"},  # near dup
        {"instruction": "Tell me about EC2", "response": "EC2 provides virtual compute in the cloud."},
        {"instruction": "Describe EC2", "response": "EC2 provides virtual computing in the cloud."},     # near dup
        {"instruction": "Completely different topic", "response": "Machine learning algorithms."},
    ]
    p = tmp_path / "near_dupes.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


class TestRecordToText:
    def test_basic_flattening(self):
        record = {"instruction": "Hello World", "response": "This is a test"}
        text = _record_to_text(record)
        assert "hello world" in text
        assert "this is a test" in text

    def test_none_values_skipped(self):
        record = {"instruction": "Hello", "response": None, "category": "test"}
        text = _record_to_text(record)
        assert "hello" in text
        assert "none" not in text.lower()

    def test_non_string_values_included(self):
        record = {"text": "hello", "count": 42}
        text = _record_to_text(record)
        assert "42" in text


class TestTextToShingles:
    def test_produces_shingles(self):
        shingles = _text_to_shingles("hello world", k=3)
        assert isinstance(shingles, set)
        assert len(shingles) > 0
        assert "hel" in shingles

    def test_short_text(self):
        shingles = _text_to_shingles("ab", k=3)
        assert len(shingles) >= 1

    def test_empty_text(self):
        shingles = _text_to_shingles("", k=3)
        assert isinstance(shingles, set)


class TestComputeMinHash:
    def test_same_text_same_hash(self):
        mh1 = _compute_minhash("hello world test", 64)
        mh2 = _compute_minhash("hello world test", 64)
        assert mh1.jaccard(mh2) > 0.95

    def test_different_texts_different_hash(self):
        mh1 = _compute_minhash("completely different text A B C", 64)
        mh2 = _compute_minhash("totally unrelated content X Y Z", 64)
        assert mh1.jaccard(mh2) < 0.5

    def test_similar_texts_high_similarity(self):
        mh1 = _compute_minhash("what is amazon s3 object storage service", 128)
        mh2 = _compute_minhash("what is amazon s3 object storage services", 128)
        assert mh1.jaccard(mh2) > 0.6


class TestDetectDuplicates:
    def test_clean_dataset_no_duplicates(self, clean_jsonl):
        result = detect_duplicates(clean_jsonl)
        assert result["exact_duplicate_count"] == 0
        assert result["total_records"] == 20

    def test_exact_duplicates_detected(self, duplicate_jsonl):
        result = detect_duplicates(duplicate_jsonl)
        assert result["exact_duplicate_count"] == 3
        assert result["total_records"] == 8

    def test_duplicate_rate_computed(self, duplicate_jsonl):
        result = detect_duplicates(duplicate_jsonl)
        assert result["duplicate_rate"] > 0

    def test_result_has_required_keys(self, clean_jsonl):
        result = detect_duplicates(clean_jsonl)
        required_keys = [
            "total_records", "exact_duplicate_count", "near_duplicate_count",
            "duplicate_rate", "duration_ms",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_custom_threshold(self, near_duplicate_jsonl):
        result = detect_duplicates(near_duplicate_jsonl, similarity_threshold=0.5)
        assert result["similarity_threshold_used"] == 0.5
