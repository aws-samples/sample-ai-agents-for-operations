"""Layer 2: Finding validator — ensures every finding is anchored to real evidence.

This guardrail prevents hallucinated or logic-error findings from reaching
the Narrative Agent or any output. It cross-checks findings against raw
tool output and enforces evidence requirements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from mio_agent.models.findings import Finding, FindingSeverity
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Minimum evidence string length — prevents empty or trivially short evidence
MIN_EVIDENCE_LENGTH = 10

# Phrases that indicate the evidence may be fabricated
SUSPICIOUS_EVIDENCE_PATTERNS = [
    r"according to",
    r"typically",
    r"usually",
    r"in general",
    r"it is recommended",
    r"best practice",  # evidence should be factual, not normative
]


@dataclass
class ValidationResult:
    """Result of finding validation."""

    is_valid: bool
    finding_id: str
    issues: list[str] = field(default_factory=list)
    was_modified: bool = False


@dataclass
class ValidationReport:
    """Aggregated validation report for a set of findings."""

    total: int
    valid: int
    invalid: int
    modified: int
    rejected_findings: list[str] = field(default_factory=list)
    issues_by_finding: dict[str, list[str]] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return round(self.valid / self.total, 3)


def validate_findings(
    findings: list[Finding],
    raw_tool_outputs: dict[str, Any] | None = None,
) -> tuple[list[Finding], ValidationReport]:
    """Validate all findings and return only those that pass.

    Args:
        findings: List of findings to validate.
        raw_tool_outputs: Optional dict of raw API response data for cross-checking.

    Returns:
        Tuple of (valid_findings, validation_report).
    """
    valid: list[Finding] = []
    results: list[ValidationResult] = []

    # Deduplicate findings before validation
    findings = _deduplicate_findings(findings)

    for finding in findings:
        result = _validate_single_finding(finding, raw_tool_outputs)
        results.append(result)
        if result.is_valid:  # noqa: SIM103 # nosemgrep: is-function-without-parentheses - is_valid is a bool field on ValidationResult dataclass, not a method
            valid.append(finding)

    invalid_count = sum(1 for r in results if not r.is_valid)  # noqa # nosemgrep: is-function-without-parentheses
    modified_count = sum(1 for r in results if r.was_modified)
    rejected = [r.finding_id for r in results if not r.is_valid]  # noqa # nosemgrep: is-function-without-parentheses
    issues = {r.finding_id: r.issues for r in results if r.issues}

    report = ValidationReport(
        total=len(findings),
        valid=len(valid),
        invalid=invalid_count,
        modified=modified_count,
        rejected_findings=rejected,
        issues_by_finding=issues,
    )

    if invalid_count > 0:
        logger.warning(
            "Finding validation rejected findings",
            extra={"rejected_count": invalid_count, "total": len(findings)},
        )

    return valid, report


def _validate_single_finding(
    finding: Finding,
    raw_tool_outputs: dict[str, Any] | None,
) -> ValidationResult:
    """Validate a single finding."""
    issues: list[str] = []

    # Rule 1: Evidence must be substantive
    if len(finding.evidence.strip()) < MIN_EVIDENCE_LENGTH:
        issues.append(f"Evidence too short ({len(finding.evidence)} chars, min {MIN_EVIDENCE_LENGTH})")

    # Rule 2: Evidence must not contain normative language (should be factual)
    evidence_lower = finding.evidence.lower()
    for pattern in SUSPICIOUS_EVIDENCE_PATTERNS:
        if re.search(pattern, evidence_lower):
            issues.append(f"Evidence contains normative language: '{pattern}' — evidence must be factual API data")
            break

    # Rule 3: Gap description must be specific (not generic)
    if finding.gap.lower() in ("monitoring gap", "observability gap", "gap identified", "issue found"):
        issues.append("Gap description is too generic — must be specific")

    # Rule 4: Recommendation must not contain cost estimates or pricing
    rec_lower = finding.recommendation.lower()
    cost_patterns = [r"\$\d+", r"cost[s]?\s+\$", r"per month", r"pricing"]
    for pattern in cost_patterns:
        if re.search(pattern, rec_lower):
            issues.append(f"Recommendation contains cost/pricing language — outside MIO Agent scope")
            break

    # Rule 5: Recommendation must not contain security vulnerability language
    security_patterns = [
        r"vulnerability", r"exploit", r"attack vector",
        r"cve-\d{4}", r"security breach",
    ]
    for pattern in security_patterns:
        if re.search(pattern, rec_lower):
            issues.append("Recommendation contains security vulnerability language — outside MIO Agent scope")
            break

    # Rule 6: If resource_arn provided, validate basic ARN format
    if finding.resource_arn and not finding.resource_arn.startswith("arn:"):
        issues.append(f"resource_arn has invalid format: {finding.resource_arn}")

    # Rule 7: CRITICAL severity requires specific evidence (not generic)
    if finding.severity == FindingSeverity.CRITICAL:
        if len(finding.evidence.strip()) < 30:
            issues.append("CRITICAL findings require detailed evidence (min 30 chars)")

    is_valid = len(issues) == 0

    if not is_valid:
        logger.debug(
            "Finding failed validation",
            extra={"finding_id": finding.finding_id, "issues": issues},
        )

    return ValidationResult(
        is_valid=is_valid,
        finding_id=finding.finding_id,
        issues=issues,
    )


def _deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """Remove duplicate findings (same resource + same gap)."""
    seen: set[tuple[str | None, str]] = set()
    unique: list[Finding] = []

    for finding in findings:
        key = (finding.resource_arn, finding.gap[:50])
        if key not in seen:
            seen.add(key)
            unique.append(finding)
        else:
            logger.debug("Deduplicated finding", extra={"gap": finding.gap[:50]})

    if len(unique) < len(findings):
        logger.info(
            "Deduplicated findings",
            extra={"original": len(findings), "deduplicated": len(unique)},
        )

    return unique
