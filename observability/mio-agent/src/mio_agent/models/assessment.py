"""Assessment request and Observability Maturity Score (OMS) data models."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class AccessTier(str, Enum):
    """Data access tier for assessment."""

    TIER1 = "tier1"  # AWS internal signals only (no customer action)
    TIER2 = "tier2"  # TAM-uploaded artifacts
    TIER3 = "tier3"  # Read-only IAM role in customer account


class TriggerType(str, Enum):
    """What triggered this assessment."""

    SCHEDULED = "scheduled"
    SUPPORT_CASE = "support_case"
    DEPLOYMENT = "deployment"
    HEALTH_EVENT = "health_event"
    ON_DEMAND = "on_demand"


class OutputAudience(str, Enum):
    """Target audience for report generation."""

    TAM = "tam"
    CUSTOMER = "customer"
    LEADERSHIP = "leadership"


class RiskLevel(str, Enum):
    """Overall risk level derived from OMS score."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ConfidenceLevel(str, Enum):
    """Confidence in assessment results based on access tier."""

    LOW = "LOW"      # Tier 1 only
    MEDIUM = "MEDIUM"  # Tier 2 artifacts
    HIGH = "HIGH"    # Tier 3 live account access


class AssessmentRequest(BaseModel):
    """Request to assess the observability posture of a customer account."""

    assessment_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this assessment run",
    )
    account_id: str = Field(
        description="12-digit AWS account ID of the customer",
    )
    account_name: str = Field(
        description="Human-readable customer account name",
        min_length=1,
        max_length=256,
    )
    access_tier: AccessTier = Field(
        description="Data access tier determining assessment depth",
    )
    role_arn: str | None = Field(
        default=None,
        description="ARN of read-only IAM role in customer account (required for tier3)",
    )
    trigger_type: TriggerType = Field(
        description="What triggered this assessment",
    )
    trigger_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context about the trigger (e.g., support case ID)",
    )
    requested_by: str = Field(
        description="Alias or identifier of the TAM/SA requesting the assessment",
        min_length=1,
        max_length=128,
    )
    output_audience: list[OutputAudience] = Field(
        default_factory=lambda: [OutputAudience.TAM],
        description="Target audiences for report generation",
        min_length=1,
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Timestamp when the request was created",
    )

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, v: str) -> str:
        """Validate AWS account ID is exactly 12 digits."""
        if not re.match(r"^\d{12}$", v):
            raise ValueError(f"account_id must be exactly 12 digits, got: {v!r}")
        return v

    @field_validator("role_arn")
    @classmethod
    def validate_role_arn(cls, v: str | None) -> str | None:
        """Validate IAM role ARN format if provided."""
        if v is not None:
            arn_pattern = r"^arn:aws:iam::\d{12}:role/[\w+=,.@/-]+$"
            if not re.match(arn_pattern, v):
                raise ValueError(f"role_arn must be a valid IAM role ARN, got: {v!r}")
        return v

    @model_validator(mode="after")
    def validate_tier3_requires_role_arn(self) -> "AssessmentRequest":
        """Tier 3 access requires a role ARN."""
        if self.access_tier == AccessTier.TIER3 and not self.role_arn:
            raise ValueError("role_arn is required when access_tier is tier3")
        return self

    model_config = {"use_enum_values": False}


class DimensionScore(BaseModel):
    """Score and findings for a single observability dimension."""

    score: float = Field(
        description="Dimension score between 1.0 (poor) and 5.0 (excellent)",
        ge=1.0,
        le=5.0,
    )
    weight: float = Field(
        description="Weight of this dimension in the overall OMS calculation",
        gt=0.0,
        le=1.0,
    )
    finding_count: int = Field(
        default=0,
        description="Number of findings in this dimension",
        ge=0,
    )
    finding_ids: list[str] = Field(
        default_factory=list,
        description="List of finding IDs associated with this dimension",
    )
    summary: str = Field(
        default="",
        description="Human-readable summary of dimension assessment",
    )

    @field_validator("score")
    @classmethod
    def round_score(cls, v: float) -> float:
        """Round score to one decimal place."""
        return round(v, 1)


class OMS(BaseModel):
    """Observability Maturity Score — the primary output of an assessment."""

    assessment_id: str = Field(description="ID of the assessment that produced this OMS")
    account_id: str = Field(description="AWS account ID that was assessed")
    account_name: str = Field(description="Human-readable account name")
    assessment_timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the assessment was performed",
    )
    overall_oms: float = Field(
        description="Overall Observability Maturity Score between 1.0 and 5.0",
        ge=1.0,
        le=5.0,
    )
    dimensions: dict[str, DimensionScore] = Field(
        description="Per-dimension scores. Keys: metrics_coverage, alerting_quality, "
        "log_intelligence, distributed_tracing, incident_readiness",
    )
    risk_level: RiskLevel = Field(
        description="Risk classification derived from overall OMS",
    )
    access_tier_used: AccessTier = Field(
        description="Access tier used for this assessment",
    )
    confidence: ConfidenceLevel = Field(
        description="Confidence level in assessment accuracy",
    )
    previous_oms: float | None = Field(
        default=None,
        description="Previous OMS score for trend comparison",
        ge=1.0,
        le=5.0,
    )
    trend: str | None = Field(
        default=None,
        description="Trend direction: IMPROVING, DECLINING, STABLE, or None if no history",
    )
    total_findings: int = Field(
        default=0,
        description="Total number of findings across all dimensions",
        ge=0,
    )

    @field_validator("overall_oms")
    @classmethod
    def round_overall_oms(cls, v: float) -> float:
        """Round OMS to one decimal place."""
        return round(v, 1)

    @field_validator("account_id")
    @classmethod
    def validate_account_id(cls, v: str) -> str:
        """Validate AWS account ID is exactly 12 digits."""
        if not re.match(r"^\d{12}$", v):
            raise ValueError(f"account_id must be exactly 12 digits, got: {v!r}")
        return v

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls, v: dict[str, DimensionScore]) -> dict[str, DimensionScore]:
        """Validate required dimensions are present."""
        required = {
            "metrics_coverage",
            "alerting_quality",
            "log_intelligence",
            "distributed_tracing",
            "incident_readiness",
        }
        missing = required - set(v.keys())
        if missing:
            raise ValueError(f"Missing required dimensions: {missing}")
        weights = sum(d.weight for d in v.values())
        if not (0.99 <= weights <= 1.01):
            raise ValueError(f"Dimension weights must sum to 1.0, got {weights:.3f}")
        return v

    @field_validator("trend")
    @classmethod
    def validate_trend(cls, v: str | None) -> str | None:
        """Validate trend value."""
        valid_trends = {"IMPROVING", "DECLINING", "STABLE", None}
        if v not in valid_trends:
            raise ValueError(f"trend must be one of {valid_trends}, got: {v!r}")
        return v

    @classmethod
    def derive_risk_level(cls, oms_score: float) -> RiskLevel:
        """Derive risk level from OMS score."""
        if oms_score >= 4.0:
            return RiskLevel.LOW
        elif oms_score >= 3.0:
            return RiskLevel.MEDIUM
        elif oms_score >= 2.0:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    @classmethod
    def derive_confidence(cls, tier: AccessTier) -> ConfidenceLevel:
        """Derive confidence level from access tier."""
        mapping = {
            AccessTier.TIER1: ConfidenceLevel.LOW,
            AccessTier.TIER2: ConfidenceLevel.MEDIUM,
            AccessTier.TIER3: ConfidenceLevel.HIGH,
        }
        return mapping[tier]

    model_config = {"use_enum_values": False}
