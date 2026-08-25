"""Finding data models for observability gap analysis."""

from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class FindingSeverity(str, Enum):
    """Severity level of an observability finding."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class FindingDimension(str, Enum):
    """Observability dimension a finding belongs to."""

    METRICS_COVERAGE = "metrics_coverage"
    ALERTING_QUALITY = "alerting_quality"
    LOG_INTELLIGENCE = "log_intelligence"
    DISTRIBUTED_TRACING = "distributed_tracing"
    INCIDENT_READINESS = "incident_readiness"


class FindingEffort(str, Enum):
    """Implementation effort to remediate a finding."""

    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class FindingSource(str, Enum):
    """Which agent/tool produced this finding."""

    CLOUDWATCH_ANALYST = "cloudwatch_analyst"
    IAC_SCANNER = "iac_scanner"
    THIRD_PARTY_VALIDATOR = "third_party_validator"
    ACCOUNT_DISCOVERY = "account_discovery"


class Finding(BaseModel):
    """A specific observability gap identified during assessment."""

    finding_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique identifier for this finding",
    )
    dimension: FindingDimension = Field(
        description="Observability dimension this finding belongs to",
    )
    severity: FindingSeverity = Field(
        description="Severity level of this finding",
    )
    resource_arn: str | None = Field(
        default=None,
        description="ARN of the affected AWS resource",
    )
    resource_type: str | None = Field(
        default=None,
        description="CloudFormation resource type (e.g. AWS::Lambda::Function)",
    )
    resource_name: str | None = Field(
        default=None,
        description="Human-readable resource name",
    )
    gap: str = Field(
        description="Short description of what is missing or misconfigured",
        min_length=1,
        max_length=512,
    )
    evidence: str = Field(
        description="Specific evidence supporting this finding (API response values, config details)",
        min_length=1,
    )
    impact: str = Field(
        description="Business/operational impact of this gap",
        min_length=1,
    )
    recommendation: str = Field(
        description="Specific, implementation-ready recommendation to remediate the gap",
        min_length=1,
    )
    effort: FindingEffort = Field(
        default=FindingEffort.MEDIUM,
        description="Estimated implementation effort to remediate",
    )
    aws_service_recommendation: str | None = Field(
        default=None,
        description="AWS service recommended to address this gap",
    )
    documentation_url: str | None = Field(
        default=None,
        description="URL to relevant AWS documentation",
    )
    source: FindingSource | None = Field(
        default=None,
        description="Which agent/tool produced this finding",
    )
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Optional metadata tags for filtering and grouping",
    )

    @field_validator("resource_arn")
    @classmethod
    def validate_resource_arn(cls, v: str | None) -> str | None:
        """Loosely validate ARN format if provided."""
        if v is not None and not v.startswith("arn:"):
            raise ValueError(f"resource_arn must start with 'arn:', got: {v!r}")
        return v

    @field_validator("documentation_url")
    @classmethod
    def validate_documentation_url(cls, v: str | None) -> str | None:
        """Validate documentation URL starts with https if provided."""
        if v is not None and not v.startswith("https://"):
            raise ValueError(f"documentation_url must start with 'https://', got: {v!r}")
        return v

    @property
    def severity_weight(self) -> float:
        """Numeric weight for scoring calculations."""
        weights = {
            FindingSeverity.CRITICAL: 2.0,
            FindingSeverity.HIGH: 1.0,
            FindingSeverity.MEDIUM: 0.5,
            FindingSeverity.LOW: 0.2,
            FindingSeverity.INFO: 0.0,
        }
        return weights[self.severity]

    model_config = {"use_enum_values": False}


class FindingCollection(BaseModel):
    """A collection of findings from an assessment."""

    assessment_id: str = Field(description="ID of the assessment these findings belong to")
    account_id: str = Field(description="AWS account ID that was assessed")
    findings: list[Finding] = Field(
        default_factory=list,
        description="All findings from the assessment",
    )

    @property
    def critical_count(self) -> int:
        """Number of CRITICAL severity findings."""
        return sum(1 for f in self.findings if f.severity == FindingSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        """Number of HIGH severity findings."""
        return sum(1 for f in self.findings if f.severity == FindingSeverity.HIGH)

    @property
    def by_dimension(self) -> dict[FindingDimension, list[Finding]]:
        """Group findings by dimension."""
        result: dict[FindingDimension, list[Finding]] = {d: [] for d in FindingDimension}
        for finding in self.findings:
            result[finding.dimension].append(finding)
        return result

    @property
    def top_3(self) -> list[Finding]:
        """Return top 3 highest severity findings."""
        severity_order = {
            FindingSeverity.CRITICAL: 0,
            FindingSeverity.HIGH: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.LOW: 3,
            FindingSeverity.INFO: 4,
        }
        sorted_findings = sorted(
            self.findings,
            key=lambda f: severity_order[f.severity],
        )
        return sorted_findings[:3]

    def add(self, finding: Finding) -> None:
        """Add a finding to the collection."""
        self.findings.append(finding)

    def filter_by_dimension(self, dimension: FindingDimension) -> list[Finding]:
        """Return findings for a specific dimension."""
        return [f for f in self.findings if f.dimension == dimension]

    def filter_by_severity(self, severity: FindingSeverity) -> list[Finding]:
        """Return findings of a specific severity."""
        return [f for f in self.findings if f.severity == severity]
