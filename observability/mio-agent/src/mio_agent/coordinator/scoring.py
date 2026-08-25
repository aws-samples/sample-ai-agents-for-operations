"""OMS (Observability Maturity Score) calculation engine."""

from __future__ import annotations

from mio_agent.models.assessment import (
    ConfidenceLevel,
    DimensionScore,
    OMS,
    AccessTier,
    RiskLevel,
)
from mio_agent.models.findings import Finding, FindingSeverity
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Dimension weights — must sum to 1.0
DIMENSION_WEIGHTS: dict[str, float] = {
    "metrics_coverage": 0.25,
    "alerting_quality": 0.25,
    "log_intelligence": 0.20,
    "distributed_tracing": 0.20,
    "incident_readiness": 0.10,
}

# Score penalty per finding severity
SEVERITY_PENALTY: dict[FindingSeverity, float] = {
    FindingSeverity.CRITICAL: 1.0,
    FindingSeverity.HIGH: 0.5,
    FindingSeverity.MEDIUM: 0.25,
    FindingSeverity.LOW: 0.10,
    FindingSeverity.INFO: 0.0,
}

# OMS score boundaries
OMS_MIN = 1.0
OMS_MAX = 5.0


def calculate_dimension_score(
    findings: list[Finding],
    resource_count: int = 1,
) -> float:
    """Calculate a dimension score from findings.

    Starts at 5.0 and deducts penalty per finding, normalized by
    resource count to avoid penalizing large accounts disproportionately.

    Args:
        findings: List of findings in this dimension.
        resource_count: Total resources evaluated in this dimension.

    Returns:
        Score between 1.0 and 5.0.
    """
    base_score = OMS_MAX
    normalized_count = max(resource_count, 1)

    for finding in findings:
        penalty = SEVERITY_PENALTY[finding.severity]
        # Normalize: cap deduction per finding relative to total resources
        normalized_penalty = (penalty / normalized_count) * 10
        base_score -= normalized_penalty

    score = round(max(OMS_MIN, base_score), 1)
    logger.debug(
        "Calculated dimension score",
        extra={"finding_count": len(findings), "resource_count": resource_count, "score": score},
    )
    return score


def calculate_oms(dimension_scores: dict[str, float]) -> float:
    """Calculate the overall Observability Maturity Score (OMS).

    Args:
        dimension_scores: Map of dimension name to score (1.0-5.0).

    Returns:
        Weighted OMS score between 1.0 and 5.0.

    Raises:
        ValueError: If required dimensions are missing.
    """
    missing = set(DIMENSION_WEIGHTS.keys()) - set(dimension_scores.keys())
    if missing:
        raise ValueError(f"Missing required dimensions for OMS calculation: {missing}")

    oms = sum(
        score * DIMENSION_WEIGHTS[dim]
        for dim, score in dimension_scores.items()
        if dim in DIMENSION_WEIGHTS
    )
    result = round(max(OMS_MIN, min(OMS_MAX, oms)), 1)
    logger.info("Calculated OMS", extra={"oms": result})
    return result


def classify_risk_level(oms_score: float) -> RiskLevel:
    """Classify risk level from OMS score.

    Args:
        oms_score: OMS score between 1.0 and 5.0.

    Returns:
        Risk level classification.
    """
    if oms_score >= 4.0:
        return RiskLevel.LOW
    elif oms_score >= 3.0:
        return RiskLevel.MEDIUM
    elif oms_score >= 2.0:
        return RiskLevel.HIGH
    else:
        return RiskLevel.CRITICAL


def calculate_trend(current: float, previous: float | None) -> str | None:
    """Calculate OMS trend direction.

    Args:
        current: Current OMS score.
        previous: Previous OMS score, or None if no history.

    Returns:
        "IMPROVING", "DECLINING", "STABLE", or None if no previous data.
    """
    if previous is None:
        return None

    delta = current - previous
    if delta > 0.2:
        return "IMPROVING"
    elif delta < -0.2:
        return "DECLINING"
    else:
        return "STABLE"


def build_dimension_scores(
    findings_by_dimension: dict[str, list[Finding]],
    resource_counts: dict[str, int] | None = None,
) -> dict[str, DimensionScore]:
    """Build DimensionScore objects from findings grouped by dimension.

    Args:
        findings_by_dimension: Map of dimension name to list of findings.
        resource_counts: Optional map of dimension name to resource count.

    Returns:
        Map of dimension name to DimensionScore.
    """
    if resource_counts is None:
        resource_counts = {}

    dimension_scores: dict[str, DimensionScore] = {}

    for dim, weight in DIMENSION_WEIGHTS.items():
        dim_findings = findings_by_dimension.get(dim, [])
        resource_count = resource_counts.get(dim, 1)
        score = calculate_dimension_score(dim_findings, resource_count)

        dimension_scores[dim] = DimensionScore(
            score=score,
            weight=weight,
            finding_count=len(dim_findings),
            finding_ids=[f.finding_id for f in dim_findings],
        )

    return dimension_scores


def build_oms(
    assessment_id: str,
    account_id: str,
    account_name: str,
    findings_by_dimension: dict[str, list[Finding]],
    access_tier: AccessTier,
    resource_counts: dict[str, int] | None = None,
    previous_oms: float | None = None,
) -> OMS:
    """Build a complete OMS object from assessment findings.

    Args:
        assessment_id: ID of the assessment.
        account_id: AWS account ID.
        account_name: Customer account name.
        findings_by_dimension: Findings grouped by dimension name.
        access_tier: Access tier used for assessment.
        resource_counts: Resources evaluated per dimension.
        previous_oms: Previous OMS score for trend calculation.

    Returns:
        Complete OMS object with scores, risk level, and trend.
    """
    dimension_scores = build_dimension_scores(findings_by_dimension, resource_counts)
    raw_scores = {dim: ds.score for dim, ds in dimension_scores.items()}
    overall = calculate_oms(raw_scores)
    risk = classify_risk_level(overall)
    trend = calculate_trend(overall, previous_oms)
    confidence = OMS.derive_confidence(access_tier)

    total_findings = sum(len(v) for v in findings_by_dimension.values())

    return OMS(
        assessment_id=assessment_id,
        account_id=account_id,
        account_name=account_name,
        overall_oms=overall,
        dimensions=dimension_scores,
        risk_level=risk,
        access_tier_used=access_tier,
        confidence=confidence,
        previous_oms=previous_oms,
        trend=trend,
        total_findings=total_findings,
    )
