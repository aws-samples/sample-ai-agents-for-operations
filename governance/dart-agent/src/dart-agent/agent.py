# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
DART Agent — Dataset Audit & Readiness for Training

Strands-based AI agent that performs pre-flight validation of LLM training
datasets before fine-tuning jobs are submitted. Prevents expensive failed
training runs by catching data quality issues upfront.

Entry points:
  - lambda_handler: ECS Fargate task / HTTP handler
  - run_preflight: Direct Python invocation
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from strands import Agent, tool
from strands.models import BedrockModel

from audit import write_audit_record
from config import Config
from tools.apply_safe_fixes import apply_safe_fixes as _apply_safe_fixes
from tools.check_training_viability import check_training_viability as _check_viability
from tools.detect_duplicates import detect_duplicates as _detect_duplicates
from tools.estimate_training_cost import estimate_training_cost as _estimate_cost
from tools.generate_preflight_report import generate_preflight_report as _generate_report
from tools.profile_dataset import profile_dataset as _profile_dataset
from tools.scan_pii import scan_pii as _scan_pii

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Load system prompt ────────────────────────────────────────────────────────
_PROMPT_PATH = Path(__file__).parent / "prompts" / "system_prompt.md"
SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8") if _PROMPT_PATH.exists() else ""


# ── Tool wrappers (Strands @tool decorators) ──────────────────────────────────

@tool
def profile_dataset(dataset_path: str, target_model: str = "anthropic.claude-sonnet-4-20250514-v1:0") -> dict:
    """
    Analyse a training dataset and return a comprehensive structural profile.

    Detects format, schema, null rates, text length distributions, token
    estimates for the target model, encoding issues, and empty records.

    Args:
        dataset_path: S3 path (s3://bucket/key) or local file path
        target_model: Bedrock model ID being targeted for fine-tuning

    Returns:
        Profile dict with format, schema, text_stats, encoding, quality_flags
    """
    return _profile_dataset(dataset_path, target_model)


@tool
def detect_duplicates(
    dataset_path: str,
    similarity_threshold: float = 0.85,
) -> dict:
    """
    Detect exact and near-duplicate records in a training dataset.

    Uses SHA-256 for exact matching and MinHash LSH for near-duplicate
    detection based on character shingle similarity.

    Args:
        dataset_path: Local file path to the dataset
        similarity_threshold: Jaccard similarity threshold (0.0-1.0, default 0.85)

    Returns:
        Dict with exact_duplicate_count, near_duplicate_count, duplicate_rate
    """
    return _detect_duplicates(dataset_path, similarity_threshold)


@tool
def scan_pii(
    dataset_path: str,
    redaction_strategy: str = "mask",
    produce_redacted_copy: bool = False,
    output_path: str = "",
) -> dict:
    """
    Detect and optionally redact PII in a training dataset using Amazon Comprehend.

    Scans all text fields for PII entity types. When produce_redacted_copy is True,
    writes a new file with PII masked or records removed. Original is never modified.

    Args:
        dataset_path: Local file path to the dataset
        redaction_strategy: 'mask' (replace with [REDACTED_TYPE]) or 'remove' (delete record)
        produce_redacted_copy: Whether to write a redacted output file
        output_path: Path for redacted output (required if produce_redacted_copy=True)

    Returns:
        Dict with pii_found, affected_record_count, entity_type_counts
    """
    return _scan_pii(
        dataset_path,
        redaction_strategy=redaction_strategy,
        produce_redacted_copy=produce_redacted_copy,
        output_path=output_path or None,
    )


@tool
def check_training_viability(
    dataset_path: str,
    target_model: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    validation_path: str = "",
) -> dict:
    """
    Validate whether a dataset is structurally ready for LLM fine-tuning.

    Checks token budget vs model context limit, train/validation data leakage,
    minimum dataset size for the target model, and schema completeness.

    Args:
        dataset_path: Local file path to the training dataset
        target_model: Bedrock model ID being fine-tuned
        validation_path: Optional path to validation split for leakage detection

    Returns:
        Dict with token_budget, schema_check, size_check, leakage_check, blocking_issues
    """
    return _check_viability(
        dataset_path,
        target_model=target_model,
        validation_path=validation_path or None,
    )


@tool
def estimate_training_cost(
    total_tokens: int,
    target_model: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    instance_type: str = "ml.p4d.24xlarge",
    num_nodes: int = 4,
    epochs: int = 3,
    duplicate_rate: float = 0.0,
    empty_record_rate: float = 0.0,
    use_bedrock_managed: bool = False,
) -> dict:
    """
    Project LLM fine-tuning cost and quantify savings from dataset fixes.

    Calculates cost before and after removing duplicates and empty records,
    showing the financial impact of dataset quality improvements.

    Args:
        total_tokens: Total token count in the current dataset
        target_model: Bedrock model ID being fine-tuned
        instance_type: SageMaker instance type (e.g., ml.p4d.24xlarge)
        num_nodes: Number of training instances
        epochs: Number of training epochs
        duplicate_rate: Fraction of records that are duplicates (0.0-1.0)
        empty_record_rate: Fraction of records that are empty (0.0-1.0)
        use_bedrock_managed: If True, estimate Amazon Bedrock managed fine-tuning cost

    Returns:
        Dict with cost_before_fixes, cost_after_fixes, savings_usd, savings_percent
    """
    return _estimate_cost(
        total_tokens=total_tokens,
        target_model=target_model,
        instance_type=instance_type,
        num_nodes=num_nodes,
        epochs=epochs,
        duplicate_rate=duplicate_rate,
        empty_record_rate=empty_record_rate,
        use_bedrock_managed=use_bedrock_managed,
    )


@tool
def apply_safe_fixes(
    dataset_path: str,
    output_path: str = "",
    remove_exact_duplicates: bool = True,
    remove_empty_records: bool = True,
    fix_encoding: bool = True,
) -> dict:
    """
    Apply safe, automatically reversible fixes to a training dataset.

    Removes exact duplicates, empty/near-empty records, and normalises
    text encoding to UTF-8. Original file is never modified.

    Args:
        dataset_path: Local path to the input dataset
        output_path: Path for the fixed output file (auto-generated if empty)
        remove_exact_duplicates: Remove exact duplicate records
        remove_empty_records: Remove records with empty required fields
        fix_encoding: Normalise text encoding to UTF-8

    Returns:
        Dict with records_before, records_after, records_removed, output_path
    """
    return _apply_safe_fixes(
        dataset_path=dataset_path,
        output_path=output_path or None,
        remove_exact_duplicates=remove_exact_duplicates,
        remove_empty_records=remove_empty_records,
        fix_encoding=fix_encoding,
    )


@tool
def generate_preflight_report(
    dataset_path: str,
    target_model: str,
    profile_result: str,
    duplicates_result: str,
    pii_result: str,
    viability_result: str,
    cost_result: str,
    run_id: str = "",
) -> dict:
    """
    Synthesise all DART analysis results into a Pre-Flight Report.

    Produces a structured JSON report and human-readable narrative with a
    final recommendation: GO, GO WITH FIXES, or NO-GO.

    Args:
        dataset_path: Path to the analysed dataset (for display)
        target_model: Target model ID (for display)
        profile_result: JSON string of profile_dataset output
        duplicates_result: JSON string of detect_duplicates output
        pii_result: JSON string of scan_pii output
        viability_result: JSON string of check_training_viability output
        cost_result: JSON string of estimate_training_cost output
        run_id: Optional run identifier

    Returns:
        Full Pre-Flight Report dict with recommendation, quality_score, findings, narrative
    """
    return _generate_report(
        dataset_path=dataset_path,
        target_model=target_model,
        profile_result=json.loads(profile_result) if isinstance(profile_result, str) else profile_result,
        duplicates_result=json.loads(duplicates_result) if isinstance(duplicates_result, str) else duplicates_result,
        pii_result=json.loads(pii_result) if isinstance(pii_result, str) else pii_result,
        viability_result=json.loads(viability_result) if isinstance(viability_result, str) else viability_result,
        cost_result=json.loads(cost_result) if isinstance(cost_result, str) else cost_result,
        run_id=run_id or None,
    )


# ── Agent factory ─────────────────────────────────────────────────────────────

def build_agent() -> Agent:
    """Construct and return a configured DART Strands Agent."""
    model = BedrockModel(
        model_id=Config.MODEL_ID,
        region_name=Config.AWS_REGION,
        max_tokens=Config.MAX_TOKENS,
    )
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            profile_dataset,
            detect_duplicates,
            scan_pii,
            check_training_viability,
            estimate_training_cost,
            apply_safe_fixes,
            generate_preflight_report,
        ],
    )


# ── Public interface ──────────────────────────────────────────────────────────

def run_preflight(user_message: str) -> str:
    """
    Run the DART agent with a natural language request.

    Args:
        user_message: Natural language description of what to check,
            e.g. "Check s3://my-bucket/train.jsonl for fine-tuning llama3-70b"

    Returns:
        Agent response string
    """
    agent = build_agent()
    response = agent(user_message)
    return str(response)


# ── Request handler ───────────────────────────────────────────────────────────

def handle_request(event: dict, context: Any = None) -> dict:
    """
    Entry handler for the DART agent.

    The reference deployment runs this as an Amazon ECS Fargate task (see
    infra/); the event-in/JSON-out shape also makes it callable from an
    AWS Lambda adapter or a local invocation if you choose that compute model.

    Accepts:
        event = { "message": "Check s3://bucket/train.jsonl for llama3-70b fine-tuning" }

    Returns:
        { "statusCode": 200, "run_id": "...", "response": "..." }

    Note: run_id is always generated server-side; any caller-supplied run_id is
    ignored so it cannot collide with or overwrite another run's audit records.
    """
    run_id = f"dart-{uuid.uuid4().hex[:8]}"
    message = event.get("message", "")

    if not message:
        return {
            "statusCode": 400,
            "error": "Missing 'message' field in request body",
        }

    logger.info(json.dumps({
        "event": "request_received",
        "run_id": run_id,
        "message_preview": message[:200],
    }))

    try:
        response = run_preflight(message)
        logger.info(json.dumps({
            "event": "request_complete",
            "run_id": run_id,
            "success": True,
        }))
        # Persist a tamper-evident audit record for this run (Article 10).
        # Best-effort: a failure to write audit data does not fail the run.
        write_audit_record(run_id=run_id, message=message, status="complete")
        return {
            "statusCode": 200,
            "run_id": run_id,
            "response": response,
        }
    except Exception as e:
        # Log full detail server-side; return a generic message + correlation id
        # to the caller so internal paths/bucket names are not leaked.
        logger.error(json.dumps({
            "event": "request_error",
            "run_id": run_id,
            "error": str(e),
        }))
        write_audit_record(run_id=run_id, message=message, status="error")
        return {
            "statusCode": 500,
            "run_id": run_id,
            "error": "Internal error. See CloudWatch logs for this run_id.",
        }


# Backwards-compatible alias for adapters that expect a `lambda_handler` symbol.
lambda_handler = handle_request


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py '<message>'")
        print("Example: python agent.py 'Check s3://my-bucket/train.jsonl for llama3-70b'")
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])
    print(run_preflight(user_input))
