"""Layer 3: Confidence gate — prevents low-confidence assessments from reaching customers.

This guardrail enforces that assessment depth is transparently communicated
and that customer-facing outputs are blocked when confidence is insufficient.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from mio_agent.models.assessment import AccessTier, ConfidenceLevel, OMS, RiskLevel
from mio_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Minimum resource count per dimension before assessment is considered valid
MIN_RESOURCES_FOR_VALID_ASSESSMENT = 1

# OMS score cap per access tier — prevents inflated scores from partial data
OMS_CAPS: dict[AccessTier, float] = {
    AccessTier.TIER1: 4.0,  # Can't claim LOW risk without live data
    AccessTier.TIER2: 4.5,  # Can't claim full LOW risk without live access
    AccessTier.TIER3: 5.0,  # Full access, no cap
}

# Audiences blocked when confidence is below threshold
CUSTOMER_FACING_AUDIENCES = {"customer", "leadership"}


class GateDecision(str, Enum):
    """Decision from the confidence gate."""
    PASS = "PASS"                          # nosec B105 - enum value, not a password  # noqa
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"  # nosec B105 - enum value, not a password  # noqa
    BLOCK_CUSTOMER = "BLOCK_CUSTOMER"      # Block customer delivery, TAM only
    BLOCK_ALL = "BLOCK_ALL"               # Block all delivery, assessment invalid


@dataclass
class GateResult:
    """Result of confidence gate evaluation."""

    decision: GateDecision
    oms: OMS
    warnings: list[str]
    blocking_reasons: list[str]
    confidence_disclaimer: str
    requires_human_review: bool

    @property
    def is_blocked(self) -> bool:
        return self.decision in (GateDecision.BLOCK_CUSTOMER, GateDecision.BLOCK_ALL)

    @property
    def customer_delivery_allowed(self) -> bool:
        return self.decision in (GateDecision.PASS, GateDecision.PASS_WITH_WARNINGS)


def evaluate_confidence(
    oms: OMS,
    resource_counts: dict[str, int] | None = None,
    requested_audiences: list[str] | None = None,
) -> GateResult:
    """Evaluate whether an assessment meets confidence thresholds for delivery.

    Args:
        oms: The OMS object to evaluate.
        resource_counts: Resource counts per dimension (for completeness check).
        requested_audiences: Target audiences for this report.

    Returns:
        GateResult with decision and recommendations.
    """
    warnings: list[str] = []
    blocking_reasons: list[str] = []
    requested_audiences = requested_audiences or ["tam"]

    # Check 1: Apply OMS cap for access tier
    tier = oms.access_tier_used
    cap = OMS_CAPS[tier]
    if oms.overall_oms > cap:
        warnings.append(
            f"OMS score {oms.overall_oms} exceeds cap of {cap} for {tier.value} access. "
            f"Score adjusted to {cap} for reporting."
        )

    # Check 2: Block customer-facing output if confidence is LOW
    customer_requested = any(a in CUSTOMER_FACING_AUDIENCES for a in requested_audiences)
    if customer_requested and oms.confidence == ConfidenceLevel.LOW:
        blocking_reasons.append(
            "Customer-facing reports require at least MEDIUM confidence. "
            "Current access tier (Tier 1) provides insufficient data depth. "
            "Request customer to grant Tier 3 read-only access for customer-ready reports."
        )

    # Check 3: Warn if resource counts are suspiciously low
    if resource_counts:
        zero_dimensions = [
            dim for dim, count in resource_counts.items() if count == 0
        ]
        if zero_dimensions:
            warnings.append(
                f"Zero resources discovered in dimensions: {zero_dimensions}. "
                "These dimensions may be scored incorrectly. Verify account has active resources."
            )

    # Check 4: CRITICAL risk + LOW confidence = require human review before any delivery
    requires_human_review = (
        oms.risk_level == RiskLevel.CRITICAL
        or oms.confidence == ConfidenceLevel.LOW
        or customer_requested
    )

    # Check 5: No findings at all with CRITICAL risk is suspicious
    if oms.total_findings == 0 and oms.risk_level == RiskLevel.CRITICAL:
        blocking_reasons.append(
            "Assessment reports CRITICAL risk but found zero specific findings. "
            "This is inconsistent and suggests a data collection error."
        )

    # Build confidence disclaimer
    disclaimer = _build_disclaimer(oms, resource_counts)

    # Determine decision
    if blocking_reasons:
        decision = GateDecision.BLOCK_CUSTOMER if customer_requested else GateDecision.PASS_WITH_WARNINGS
    elif warnings:
        decision = GateDecision.PASS_WITH_WARNINGS
    else:
        decision = GateDecision.PASS

    logger.info(
        "Confidence gate evaluated",
        extra={
            "decision": decision.value,
            "oms_score": oms.overall_oms,
            "confidence": oms.confidence.value,
            "tier": tier.value,
            "warnings": len(warnings),
            "blocking_reasons": len(blocking_reasons),
        },
    )

    return GateResult(
        decision=decision,
        oms=oms,
        warnings=warnings,
        blocking_reasons=blocking_reasons,
        confidence_disclaimer=disclaimer,
        requires_human_review=requires_human_review,
    )


def _build_disclaimer(oms: OMS, resource_counts: dict[str, int] | None) -> str:
    """Build a transparency disclaimer based on assessment depth."""
    tier_descriptions = {
        AccessTier.TIER1: (
            "This assessment is based on AWS internal signals only (Tier 1 access). "
            "No live account analysis was performed. Findings are indicative, not exhaustive."
        ),
        AccessTier.TIER2: (
            "This assessment is based on uploaded artifacts (Tier 2 access). "
            "Findings are based on provided IaC templates and exports, not live account state."
        ),
        AccessTier.TIER3: (
            "This assessment is based on live read-only account access (Tier 3). "
            "Findings reflect the account state at the time of assessment."
        ),
    }

    confidence_notes = {
        ConfidenceLevel.LOW: "⚠️ CONFIDENCE: LOW — Results should be treated as preliminary indicators only.",
        ConfidenceLevel.MEDIUM: "ℹ️ CONFIDENCE: MEDIUM — Results are assessment-grade but may not reflect all resources.",
        ConfidenceLevel.HIGH: "✅ CONFIDENCE: HIGH — Results are based on comprehensive live account data.",
    }

    base = tier_descriptions.get(oms.access_tier_used, "")
    confidence = confidence_notes.get(oms.confidence, "")
    timestamp = f"Assessment timestamp: {oms.assessment_timestamp.isoformat()}Z"

    return f"{confidence}\n\n{base}\n\n{timestamp}"
