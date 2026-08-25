"""Report data models for TAM, customer, and leadership output."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from mio_agent.models.assessment import OMS, RiskLevel
from mio_agent.models.findings import Finding


class ReportFormat(str, Enum):
    """Output format for generated reports."""

    MARKDOWN = "markdown"
    PDF = "pdf"
    JSON = "json"


class ActionItem(BaseModel):
    """A specific recommended action from the assessment."""

    priority: int = Field(description="Priority rank (1 = highest)", ge=1)
    title: str = Field(description="Short title of the action")
    description: str = Field(description="Detailed description of what to do")
    effort: str = Field(description="Implementation effort estimate")
    aws_services: list[str] = Field(
        default_factory=list,
        description="AWS services involved in implementing this action",
    )
    expected_oms_improvement: float = Field(
        default=0.0,
        description="Expected OMS improvement if this action is implemented",
        ge=0.0,
        le=4.0,
    )
    finding_ids: list[str] = Field(
        default_factory=list,
        description="Finding IDs this action addresses",
    )


class TAMBrief(BaseModel):
    """TAM Weekly Brief — concise account health briefing for TAM/SA use."""

    report_id: str = Field(description="Unique report identifier")
    assessment_id: str = Field(description="Source assessment ID")
    account_id: str = Field(description="AWS account ID")
    account_name: str = Field(description="Customer account name")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    report_format: ReportFormat = Field(default=ReportFormat.MARKDOWN)

    # OMS summary
    current_oms: float = Field(description="Current OMS score", ge=1.0, le=5.0)
    previous_oms: float | None = Field(default=None, description="Previous OMS for trend")
    trend: str | None = Field(default=None, description="IMPROVING / DECLINING / STABLE")
    risk_level: RiskLevel = Field(description="Current risk classification")

    # Key content
    top_3_gaps: list[Finding] = Field(
        default_factory=list,
        description="Top 3 highest-priority gaps",
        max_length=3,
    )
    talking_points: list[str] = Field(
        default_factory=list,
        description="Ready-to-use talking points for customer conversation",
    )
    incident_context: str | None = Field(
        default=None,
        description="Context if this was triggered by a support case or incident",
    )
    recommended_aws_services: list[str] = Field(
        default_factory=list,
        description="AWS services to recommend in the customer conversation",
    )
    narrative: str = Field(
        default="",
        description="Full plain-English narrative of the brief (AI-generated)",
    )

    @property
    def trend_emoji(self) -> str:
        """Visual trend indicator."""
        mapping = {"IMPROVING": "📈", "DECLINING": "📉", "STABLE": "➡️"}
        return mapping.get(self.trend or "", "➡️")

    @property
    def risk_emoji(self) -> str:
        """Visual risk indicator."""
        mapping = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🟠",
            RiskLevel.CRITICAL: "🔴",
        }
        return mapping.get(self.risk_level, "⚪")

    model_config = {"use_enum_values": False}


class CustomerReport(BaseModel):
    """Customer Observability Health Report — for sharing with customer engineering and leadership."""

    report_id: str = Field(description="Unique report identifier")
    assessment_id: str = Field(description="Source assessment ID")
    account_id: str = Field(description="AWS account ID")
    account_name: str = Field(description="Customer account name")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    report_format: ReportFormat = Field(default=ReportFormat.MARKDOWN)

    # OMS data
    oms: OMS = Field(description="Full OMS with dimension breakdown")

    # Narrative sections
    executive_summary: str = Field(
        description="Non-technical executive summary of observability health",
    )
    technical_detail: str = Field(
        description="Technical detail section for engineering teams",
    )
    action_plan: list[ActionItem] = Field(
        default_factory=list,
        description="Prioritized action plan with implementation details",
    )

    # Impact framing
    detection_time_impact: str | None = Field(
        default=None,
        description="Estimated impact on incident detection time (e.g., '+22 min MTTD')",
    )
    before_after_narrative: str | None = Field(
        default=None,
        description="Narrative: 'If you implement these recommendations, your OMS would improve from X to Y'",
    )

    # Sharing
    report_s3_url: str | None = Field(
        default=None,
        description="Presigned S3 URL for sharing the report",
    )


class AccountRiskSummary(BaseModel):
    """Summary of a single account for the leadership portfolio view."""

    account_id: str = Field(description="AWS account ID")
    account_name: str = Field(description="Customer account name")
    oms_score: float = Field(description="Current OMS score", ge=1.0, le=5.0)
    risk_level: RiskLevel = Field(description="Risk level")
    trend: str | None = Field(default=None)
    critical_findings: int = Field(default=0, ge=0)
    high_findings: int = Field(default=0, ge=0)
    last_assessed: datetime = Field(default_factory=datetime.utcnow)
    support_cases_30d: int = Field(
        default=0,
        description="Number of support cases opened in last 30 days",
        ge=0,
    )

    model_config = {"use_enum_values": False}


class LeadershipSummary(BaseModel):
    """Leadership Portfolio Summary — aggregate observability intelligence across accounts."""

    report_id: str = Field(description="Unique report identifier")
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    report_format: ReportFormat = Field(default=ReportFormat.MARKDOWN)

    # Portfolio stats
    total_accounts: int = Field(description="Total accounts assessed", ge=0)
    accounts_by_risk: dict[str, int] = Field(
        default_factory=dict,
        description="Count of accounts per risk level: {LOW: N, MEDIUM: N, HIGH: N, CRITICAL: N}",
    )
    average_oms: float = Field(
        description="Average OMS across all assessed accounts",
        ge=1.0,
        le=5.0,
    )
    portfolio_trend: str | None = Field(
        default=None,
        description="Overall portfolio trend: IMPROVING / DECLINING / STABLE",
    )

    # Risk accounts
    high_risk_accounts: list[AccountRiskSummary] = Field(
        default_factory=list,
        description="Accounts with HIGH or CRITICAL risk level, ranked by OMS",
    )

    # Correlation
    support_case_correlation: str | None = Field(
        default=None,
        description="Narrative on correlation between low OMS scores and support case volume",
    )

    # Narrative
    narrative: str = Field(
        default="",
        description="Full plain-English leadership narrative (AI-generated)",
    )

    @property
    def critical_account_count(self) -> int:
        """Number of accounts at CRITICAL risk."""
        return self.accounts_by_risk.get("CRITICAL", 0)

    @property
    def at_risk_percentage(self) -> float:
        """Percentage of accounts at HIGH or CRITICAL risk."""
        if self.total_accounts == 0:
            return 0.0
        at_risk = self.accounts_by_risk.get("HIGH", 0) + self.accounts_by_risk.get("CRITICAL", 0)
        return round(at_risk / self.total_accounts * 100, 1)

    model_config = {"use_enum_values": False}
