# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

"""
Tool: generate_preflight_report

Synthesises all DART analysis results into a structured Pre-Flight Report
with a final GO / NO-GO / GO WITH FIXES recommendation.

Uses Amazon Bedrock (Claude Sonnet 4) to generate the human-readable
narrative summary. The structured JSON report is always produced regardless.
"""

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import boto3
import polars as pl
from botocore.exceptions import ClientError

from config import Config

logger = logging.getLogger(__name__)

# Severity scoring weights for quality score calculation
_SEVERITY_WEIGHTS = {
    "critical": 30,
    "high": 15,
    "medium": 7,
    "low": 2,
}


def _compute_quality_score(findings: list[dict]) -> int:
    """Compute a 0–100 quality score from findings list."""
    deductions = sum(
        _SEVERITY_WEIGHTS.get(f.get("severity", "low"), 2)
        for f in findings
    )
    return max(0, min(100, 100 - deductions))


def _determine_recommendation(findings: list[dict]) -> str:
    """Determine GO / NO-GO / GO WITH FIXES from findings severity."""
    severities = {f.get("severity", "low") for f in findings}
    if not findings:
        return "GO"
    if "critical" in severities:
        return "NO-GO"
    if "high" in severities or "medium" in severities:
        return "GO WITH FIXES"
    return "GO"  # only low findings


def _recommendation_emoji(rec: str) -> str:
    return {"GO": "✅", "GO WITH FIXES": "⚠️", "NO-GO": "🔴"}.get(rec, "❓")


def _build_findings(
    profile: dict,
    duplicates: dict,
    pii: dict,
    viability: dict,
    cost: dict,
) -> list[dict]:
    """Convert raw tool outputs into a normalised findings list."""
    findings = []

    # PII — critical
    if pii.get("pii_found"):
        findings.append({
            "id": "PII-001",
            "severity": "critical",
            "category": "PII / Compliance",
            "title": f"PII detected in {pii['affected_record_count']} records",
            "detail": (
                f"Entity types found: {list(pii.get('entity_type_counts', {}).keys())}. "
                f"EU AI Act Article 10 requires auditable PII controls on training data."
            ),
            "action": "Redact PII before training. Requires your approval.",
            "auto_fixable": False,
            "record_count": pii["affected_record_count"],
        })
    elif pii.get("scan_complete") is False:
        # The PII scan did not actually run (e.g. Comprehend access denied).
        # Surface this as a blocker — a false "clean" is the dangerous outcome.
        findings.append({
            "id": "PII-000",
            "severity": "critical",
            "category": "PII / Compliance",
            "title": "PII scan did NOT complete — dataset is NOT verified PII-clean",
            "detail": (
                f"The PII scan could not run ({pii.get('scan_error', 'unknown error')}). "
                f"Do not treat this dataset as free of PII. Resolve access to the PII "
                f"scanning service and re-run before training."
            ),
            "action": "Fix PII-scan access and re-run; do not train assuming the data is clean.",
            "auto_fixable": False,
            "record_count": None,
        })

    # Train/val leakage — critical
    leakage = viability.get("leakage_check", {})
    if leakage.get("leakage_detected"):
        findings.append({
            "id": "LEAK-001",
            "severity": "critical",
            "category": "Data Integrity",
            "title": f"Train/validation leakage: {leakage['leaked_record_count']} records overlap",
            "detail": (
                f"{leakage['leakage_rate']*100:.1f}% of your validation set appears in training. "
                f"This will inflate eval scores and mask overfitting."
            ),
            "action": "Re-split the dataset ensuring no record overlap.",
            "auto_fixable": False,
            "record_count": leakage["leaked_record_count"],
        })

    # Context limit exceeded — critical
    token_budget = viability.get("token_budget", {})
    if token_budget.get("records_exceeding_context", 0) > 0:
        findings.append({
            "id": "TOKEN-001",
            "severity": "critical",
            "category": "Token Budget",
            "title": f"{token_budget['records_exceeding_context']} records exceed model context limit",
            "detail": (
                f"These records have more tokens than the model's context window "
                f"({token_budget['model_context_limit']:,} tokens). They will be truncated "
                f"during training, degrading quality."
            ),
            "action": "Truncate or split these records before training.",
            "auto_fixable": False,
            "record_count": token_budget["records_exceeding_context"],
        })

    # Near-duplicates — high
    near_dup = duplicates.get("near_duplicate_count", 0)
    if near_dup > 0:
        waste = cost.get("savings_usd", 0)
        findings.append({
            "id": "DEDUP-001",
            "severity": "high",
            "category": "Duplicates",
            "title": f"{near_dup:,} near-duplicate records ({duplicates.get('duplicate_rate', 0)*100:.1f}% of dataset)",
            "detail": (
                f"Near-duplicate records cause the model to memorise patterns rather than "
                f"generalise. Estimated wasted compute: ${waste:,.0f}."
            ),
            "action": "Remove near-duplicates. Auto-fix available for exact duplicates.",
            "auto_fixable": True,
            "record_count": near_dup,
        })

    # Empty records — high
    empty = profile.get("empty_record_count", 0)
    if empty > 0:
        findings.append({
            "id": "EMPTY-001",
            "severity": "high",
            "category": "Data Quality",
            "title": f"{empty:,} records have empty or near-empty text fields",
            "detail": (
                f"Records with fewer than {Config.MIN_RESPONSE_TOKENS} tokens in text fields "
                f"contribute noise and can cause loss spikes during training."
            ),
            "action": "Remove empty records. Auto-fix available.",
            "auto_fixable": True,
            "record_count": empty,
        })

    # Schema not recognised — medium
    schema_check = viability.get("schema_check", {})
    if not schema_check.get("schema_valid"):
        findings.append({
            "id": "SCHEMA-001",
            "severity": "medium",
            "category": "Schema",
            "title": "Dataset schema does not match a known fine-tuning format",
            "detail": (
                f"Columns found: {schema_check.get('columns_found', [])}. "
                f"Known formats require fields like instruction/response, prompt/completion, "
                f"or messages (chat format)."
            ),
            "action": "Verify your dataset schema matches the target model's fine-tuning format.",
            "auto_fixable": False,
            "record_count": None,
        })

    # Encoding warning — low
    if profile.get("encoding_warning"):
        findings.append({
            "id": "ENC-001",
            "severity": "low",
            "category": "Encoding",
            "title": f"Non-UTF-8 encoding detected ({profile.get('encoding')})",
            "detail": "Mixed encodings can cause tokenisation inconsistencies during training.",
            "action": "Normalise to UTF-8. Auto-fix available.",
            "auto_fixable": True,
            "record_count": None,
        })

    # Dataset too small — medium
    size_check = viability.get("size_check", {})
    if not size_check.get("meets_minimum"):
        findings.append({
            "id": "SIZE-001",
            "severity": "medium",
            "category": "Dataset Size",
            "title": (
                f"Dataset may be too small for {size_check.get('model_size_category', '')} model "
                f"({size_check.get('total_records', 0):,} records, "
                f"recommended: {size_check.get('minimum_recommended', 0):,})"
            ),
            "detail": "Small datasets risk overfitting and poor generalisation.",
            "action": "Consider augmenting with additional data or using a smaller model.",
            "auto_fixable": False,
            "record_count": None,
        })

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: severity_order.get(f["severity"], 4))
    return findings


def _call_bedrock_narrative(
    recommendation: str,
    quality_score: int,
    findings: list[dict],
    cost_before: float,
    cost_after: float,
    savings: float,
    dataset_path: str,
    target_model: str,
) -> str:
    """Generate a concise human-readable narrative using Amazon Bedrock."""
    bedrock = boto3.client("bedrock-runtime", region_name=Config.AWS_REGION)

    # Build a compact findings payload the model can render faithfully.
    findings_payload = json.dumps(
        [
            {
                "severity": f["severity"],
                "title": f["title"],
                "detail": f.get("detail", ""),
                "action": f["action"],
                "auto_fixable": bool(f.get("auto_fixable")),
            }
            for f in findings[:8]
        ],
        indent=2,
    )

    prompt = f"""You are DART — Dataset Audit & Readiness for Training — an expert pre-flight agent for LLM training data.

Produce a polished, scannable **Markdown** pre-flight report from the analysis data below.
Optimise for at-a-glance clarity: a reader should grasp the verdict, the top risks, and the
money in ten seconds. Be precise — cite the exact record counts and dollar figures given.

DATA
Dataset: {dataset_path}
Target model: {target_model}
Recommendation: {recommendation}
Quality score: {quality_score}/100
Cost before fixes: ${cost_before:,.2f}
Cost after fixes: ${cost_after:,.2f}
Savings: ${savings:,.2f}
Findings ({len(findings)} total):
{findings_payload}

OUTPUT — use exactly this Markdown structure and nothing else (no preamble, no sign-off):

# 📊 DART Pre-Flight Report

> **{recommendation}** — Quality Score **{quality_score}/100**
> `{dataset_path}` → `{target_model}`

## ⚡ At a Glance
A one-line verdict sentence, then a compact Markdown table with columns:
`Severity | Finding | Records | Auto-fix?` — one row per finding, ordered
critical → high → medium → low. Use 🔴 critical, 🟠 high, 🟡 medium, ⚪ low in the Severity cell.

## 💰 Cost Impact
A Markdown table: `| Metric | Before | After | Savings |` with a Training Cost row
(use the dollar figures above) and a Records row if record counts are available.

## 🔧 Recommended Actions
Two short bulleted groups:
- **Auto-fixable now** — the findings marked auto_fixable, each one line.
- **Needs your decision** — the rest, each one line.

## 🔒 Compliance & Audit
One or two lines: whether PII scanning completed and what it found, and that every
run is written to an immutable audit trail (EU AI Act Article 10).
IMPORTANT: if the PII finding indicates the scan did NOT complete, say clearly
"PII scan did not complete — do not treat this dataset as PII-clean," rather than
implying no PII exists.

Keep it tight. Prefer tables and short bullets over paragraphs."""

    try:
        response = bedrock.invoke_model(
            modelId=Config.MODEL_ID,
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            }),
            contentType="application/json",
            accept="application/json",
        )
        body = json.loads(response["body"].read())
        return body["content"][0]["text"]
    except ClientError as e:
        logger.error(json.dumps({"event": "bedrock_narrative_error", "error": str(e)}))
        return f"{_recommendation_emoji(recommendation)} {recommendation} — Quality Score: {quality_score}/100. See structured findings for details."


def generate_preflight_report(
    dataset_path: str,
    target_model: str,
    profile_result: dict,
    duplicates_result: dict,
    pii_result: dict,
    viability_result: dict,
    cost_result: dict,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Synthesise all DART analysis results into a Pre-Flight Report.

    Produces both a structured JSON report and a human-readable narrative
    (via Amazon Bedrock Claude Sonnet 4). Determines the final recommendation:
    GO, GO WITH FIXES, or NO-GO.

    Args:
        dataset_path: Path to the analysed dataset (for display)
        target_model: Target model ID (for display)
        profile_result: Output from profile_dataset tool
        duplicates_result: Output from detect_duplicates tool
        pii_result: Output from scan_pii tool
        viability_result: Output from check_training_viability tool
        cost_result: Output from estimate_training_cost tool
        run_id: Optional run identifier. Generated if not provided.

    Returns:
        Dictionary with: run_id, recommendation, quality_score, findings,
        narrative, cost_summary, auto_fixable_count, timestamp, duration_ms
    """
    start_ts = time.time()
    run_id = run_id or f"dart-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"

    logger.info(json.dumps({
        "event": "tool_start",
        "tool": "generate_preflight_report",
        "run_id": run_id,
    }))

    findings = _build_findings(
        profile_result, duplicates_result, pii_result, viability_result, cost_result
    )
    recommendation = _determine_recommendation(findings)
    quality_score = _compute_quality_score(findings)

    cost_before = cost_result.get("cost_before_fixes", {}).get("estimated_cost_usd", 0)
    cost_after = cost_result.get("cost_after_fixes", {}).get("estimated_cost_usd", 0)
    savings = cost_result.get("savings_usd", 0)

    narrative = _call_bedrock_narrative(
        recommendation, quality_score, findings,
        cost_before, cost_after, savings,
        dataset_path, target_model,
    )

    auto_fixable = [f for f in findings if f.get("auto_fixable")]
    needs_approval = [f for f in findings if not f.get("auto_fixable")]

    duration_ms = round((time.time() - start_ts) * 1000, 1)
    timestamp = datetime.now(timezone.utc).isoformat()

    report = {
        "run_id": run_id,
        "timestamp": timestamp,
        "dataset_path": dataset_path,
        "target_model": target_model,
        "recommendation": recommendation,
        "recommendation_emoji": _recommendation_emoji(recommendation),
        "quality_score": quality_score,
        "findings": findings,
        "findings_count": {
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "high": sum(1 for f in findings if f["severity"] == "high"),
            "medium": sum(1 for f in findings if f["severity"] == "medium"),
            "low": sum(1 for f in findings if f["severity"] == "low"),
        },
        "auto_fixable_count": len(auto_fixable),
        "needs_approval_count": len(needs_approval),
        "cost_summary": {
            "before_fixes_usd": cost_before,
            "after_fixes_usd": cost_after,
            "savings_usd": savings,
            "savings_percent": cost_result.get("savings_percent", 0),
        },
        "dataset_summary": {
            "total_records": profile_result.get("total_records", 0),
            "format": profile_result.get("format"),
            "encoding": profile_result.get("encoding"),
        },
        "narrative": narrative,
        "duration_ms": duration_ms,
    }

    logger.info(json.dumps({
        "event": "tool_complete",
        "tool": "generate_preflight_report",
        "run_id": run_id,
        "recommendation": recommendation,
        "quality_score": quality_score,
        "findings_count": len(findings),
        "duration_ms": duration_ms,
        "success": True,
    }))

    return report
