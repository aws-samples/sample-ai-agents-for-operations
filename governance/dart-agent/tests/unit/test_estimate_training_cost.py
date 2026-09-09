# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""Unit tests for estimate_training_cost tool."""

from pathlib import Path
import pytest
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src" / "dart-agent"))

from tools.estimate_training_cost import (
    _sagemaker_cost,
    _bedrock_cost,
    estimate_training_cost,
)


class TestSageMakerCost:
    def test_basic_estimate(self):
        result = _sagemaker_cost(100_000_000, "ml.p4d.24xlarge", 4, 3)
        assert result["estimated_cost_usd"] > 0
        assert result["provider"] == "Amazon SageMaker (self-managed)"
        assert result["num_nodes"] == 4

    def test_more_nodes_costs_more(self):
        cost_1 = _sagemaker_cost(100_000_000, "ml.p4d.24xlarge", 1, 3)
        cost_4 = _sagemaker_cost(100_000_000, "ml.p4d.24xlarge", 4, 3)
        assert cost_4["estimated_cost_usd"] > cost_1["estimated_cost_usd"]

    def test_more_epochs_costs_more(self):
        cost_1 = _sagemaker_cost(100_000_000, "ml.g5.xlarge", 1, 1)
        cost_3 = _sagemaker_cost(100_000_000, "ml.g5.xlarge", 1, 3)
        assert cost_3["estimated_cost_usd"] > cost_1["estimated_cost_usd"]

    def test_unknown_instance_uses_default(self):
        result = _sagemaker_cost(1_000_000, "ml.unknown.type", 1, 1)
        assert result["estimated_cost_usd"] >= 0

    def test_result_keys(self):
        result = _sagemaker_cost(1_000_000, "ml.g5.xlarge", 1, 1)
        for key in ["provider", "instance_type", "num_nodes", "epochs",
                    "estimated_hours", "estimated_cost_usd"]:
            assert key in result


class TestBedrockCost:
    def test_basic_estimate(self):
        result = _bedrock_cost(10_000_000, "anthropic.claude-sonnet-4-20250514-v1:0", 1)
        assert result["estimated_cost_usd"] > 0
        assert result["provider"] == "Amazon Bedrock (managed fine-tuning)"

    def test_more_tokens_costs_more(self):
        cost_small = _bedrock_cost(1_000_000, "anthropic.claude-haiku-4-5", 1)
        cost_large = _bedrock_cost(10_000_000, "anthropic.claude-haiku-4-5", 1)
        assert cost_large["estimated_cost_usd"] > cost_small["estimated_cost_usd"]

    def test_unknown_model_uses_default(self):
        result = _bedrock_cost(1_000_000, "some.unknown.model", 1)
        assert result["estimated_cost_usd"] > 0


class TestEstimateTrainingCost:
    def test_savings_from_duplicates(self):
        result = estimate_training_cost(100_000_000, duplicate_rate=0.30, epochs=3)
        assert result["savings_usd"] > 0
        assert result["tokens_after_fixes"] < result["tokens_before_fixes"]

    def test_no_issues_no_savings(self):
        result = estimate_training_cost(50_000_000, duplicate_rate=0.0, empty_record_rate=0.0)
        assert result["savings_usd"] == 0.0

    def test_bedrock_managed_mode(self):
        result = estimate_training_cost(
            10_000_000, target_model="anthropic.claude-haiku-4-5", use_bedrock_managed=True
        )
        assert "Amazon Bedrock" in result["cost_before_fixes"]["provider"]

    def test_required_keys_present(self):
        result = estimate_training_cost(total_tokens=10_000_000)
        for key in ["tokens_before_fixes", "tokens_after_fixes", "cost_before_fixes",
                    "cost_after_fixes", "savings_usd", "savings_percent", "duration_ms"]:
            assert key in result, f"Missing key: {key}"

    def test_combined_rate_capped_at_99pct(self):
        result = estimate_training_cost(100_000_000, duplicate_rate=0.60, empty_record_rate=0.60)
        assert result["tokens_after_fixes"] > 0
