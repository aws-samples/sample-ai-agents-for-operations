# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Tool: estimate_training_cost

Projects LLM fine-tuning cost based on dataset token count, target model,
compute type (SageMaker self-managed vs Amazon Bedrock managed fine-tuning),
and estimated training epochs.

Shows cost before and after dataset fixes to quantify savings.
"""

import json
import logging
import time
from typing import Any

from config import Config

logger = logging.getLogger(__name__)

# ── Pricing tables ────────────────────────────────────────────────────────────
# SageMaker training instance pricing (USD/hr, on-demand, us-east-1)
# Source: AWS public pricing — update periodically
SAGEMAKER_INSTANCE_PRICING: dict[str, float] = {
    "ml.p3.2xlarge": 3.825,
    "ml.p3.8xlarge": 15.30,
    "ml.p3.16xlarge": 30.60,
    "ml.p4d.24xlarge": 32.77,
    "ml.p5.48xlarge": 98.32,
    "ml.g5.xlarge": 1.41,
    "ml.g5.12xlarge": 9.67,
    "ml.g5.48xlarge": 24.19,
    "ml.trn1.32xlarge": 21.50,
}

# Amazon Bedrock fine-tuning pricing (USD per 1000 tokens)
# Source: AWS public pricing — varies by model
BEDROCK_FINETUNING_PRICING: dict[str, float] = {
    "anthropic.claude-sonnet-4-20250514-v1:0": 0.008,
    "anthropic.claude-haiku-4-5": 0.002,
    "amazon.nova-pro-v1:0": 0.004,
    "meta.llama3-70b-instruct-v1:0": 0.0055,
    "meta.llama3-8b-instruct-v1:0": 0.0020,
    "default": 0.004,
}

# Rough tokens-per-second throughput per instance type (approximate)
TOKENS_PER_SECOND: dict[str, float] = {
    "ml.p4d.24xlarge": 150_000,
    "ml.p5.48xlarge": 400_000,
    "ml.g5.48xlarge": 80_000,
    "ml.g5.12xlarge": 25_000,
    "ml.g5.xlarge": 6_000,
    "ml.trn1.32xlarge": 200_000,
    "ml.p3.2xlarge": 12_000,
    "ml.p3.8xlarge": 40_000,
    "ml.p3.16xlarge": 80_000,
    "default": 50_000,
}


def _sagemaker_cost(
    total_tokens: int,
    instance_type: str,
    num_nodes: int,
    epochs: int,
) -> dict:
    """Estimate SageMaker self-managed training cost."""
    hourly = SAGEMAKER_INSTANCE_PRICING.get(instance_type, 3.825)
    tps = TOKENS_PER_SECOND.get(instance_type, TOKENS_PER_SECOND["default"])

    tokens_per_epoch = total_tokens
    total_tokens_processed = tokens_per_epoch * epochs
    seconds = total_tokens_processed / (tps * num_nodes)
    hours = seconds / 3600
    total_cost = hours * hourly * num_nodes

    return {
        "provider": "Amazon SageMaker (self-managed)",
        "instance_type": instance_type,
        "num_nodes": num_nodes,
        "epochs": epochs,
        "estimated_hours": round(hours, 2),
        "estimated_cost_usd": round(total_cost, 2),
        "hourly_rate_per_node_usd": hourly,
    }


def _bedrock_cost(
    total_tokens: int,
    model_id: str,
    epochs: int,
) -> dict:
    """Estimate Amazon Bedrock managed fine-tuning cost."""
    price_per_1k = BEDROCK_FINETUNING_PRICING.get(
        model_id, BEDROCK_FINETUNING_PRICING["default"]
    )
    total_tokens_processed = total_tokens * epochs
    cost = (total_tokens_processed / 1000) * price_per_1k

    return {
        "provider": "Amazon Bedrock (managed fine-tuning)",
        "model_id": model_id,
        "epochs": epochs,
        "total_tokens_billed": total_tokens_processed,
        "price_per_1k_tokens_usd": price_per_1k,
        "estimated_cost_usd": round(cost, 2),
    }


def estimate_training_cost(
    total_tokens: int,
    target_model: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    instance_type: str = "ml.p4d.24xlarge",
    num_nodes: int = 4,
    epochs: int = 3,
    duplicate_rate: float = 0.0,
    empty_record_rate: float = 0.0,
    use_bedrock_managed: bool = False,
) -> dict[str, Any]:
    """
    Project LLM fine-tuning cost and quantify savings from dataset fixes.

    Calculates training cost before and after removing duplicates and empty
    records, showing the financial impact of dataset quality improvements.

    Args:
        total_tokens: Total token count in the current (unfixed) dataset
        target_model: Bedrock model ID being fine-tuned
        instance_type: SageMaker instance type (ignored if use_bedrock_managed=True)
        num_nodes: Number of SageMaker instances (ignored if use_bedrock_managed=True)
        epochs: Number of training epochs
        duplicate_rate: Fraction of records that are duplicates (0.0–1.0)
        empty_record_rate: Fraction of records that are empty/near-empty (0.0–1.0)
        use_bedrock_managed: If True, estimate Amazon Bedrock managed fine-tuning cost

    Returns:
        Dictionary with: cost_before_fixes, cost_after_fixes, savings_usd,
        tokens_before, tokens_after, duration_ms
    """
    start_ts = time.time()
    logger.info(json.dumps({
        "event": "tool_start",
        "tool": "estimate_training_cost",
        "total_tokens": total_tokens,
        "target_model": target_model,
        "epochs": epochs,
    }))

    wasted_fraction = min(duplicate_rate + empty_record_rate, 0.99)
    tokens_after = int(total_tokens * (1 - wasted_fraction))

    if use_bedrock_managed:
        cost_before = _bedrock_cost(total_tokens, target_model, epochs)
        cost_after = _bedrock_cost(tokens_after, target_model, epochs)
    else:
        cost_before = _sagemaker_cost(total_tokens, instance_type, num_nodes, epochs)
        cost_after = _sagemaker_cost(tokens_after, instance_type, num_nodes, epochs)

    savings = round(
        cost_before["estimated_cost_usd"] - cost_after["estimated_cost_usd"], 2
    )
    savings_pct = round(savings / cost_before["estimated_cost_usd"] * 100, 1) if cost_before["estimated_cost_usd"] > 0 else 0.0

    duration_ms = round((time.time() - start_ts) * 1000, 1)

    result = {
        "tokens_before_fixes": total_tokens,
        "tokens_after_fixes": tokens_after,
        "tokens_removed": total_tokens - tokens_after,
        "cost_before_fixes": cost_before,
        "cost_after_fixes": cost_after,
        "savings_usd": savings,
        "savings_percent": savings_pct,
        "compute_type": "bedrock_managed" if use_bedrock_managed else "sagemaker_self_managed",
        "duration_ms": duration_ms,
    }

    logger.info(json.dumps({
        "event": "tool_complete",
        "tool": "estimate_training_cost",
        "cost_before": cost_before["estimated_cost_usd"],
        "cost_after": cost_after["estimated_cost_usd"],
        "savings_usd": savings,
        "duration_ms": duration_ms,
        "success": True,
    }))

    return result
