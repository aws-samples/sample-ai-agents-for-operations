# Responsible AI — MIO Agent

## Overview

MIO Agent uses generative AI to assess AWS environment observability posture and generate reports. This document describes the guardrail architecture, human-in-the-loop design, known limitations, and shared responsibility model.

This document exists because we believe responsible AI systems require transparency about how they work, where they can fail, and who is accountable for their outputs.

---

## The Five-Layer Guardrail Architecture

MIO Agent applies five layers of safety controls before any output reaches a user.

```
Customer AWS Data
        ↓
Layer 1: Input Validation
  • Prompt injection detection
  • Payload size limits
  • Account ID and ARN format validation
        ↓
Layer 2: Finding Validation (Evidence Anchoring)
  • Every finding must reference real API response data
  • Findings with normative language ("best practice") rejected
  • Duplicate findings deduplicated
  • CRITICAL findings require substantive evidence
        ↓
Layer 3: Confidence Gate
  • OMS scores capped by access tier (no FALSE LOW risk claims from partial data)
  • Customer reports blocked if confidence is LOW (Tier 1 only)
  • Zero-resource dimensions flagged as potentially incomplete
  • CRITICAL risk always requires human review
        ↓
Layer 4: Amazon Bedrock Guardrails
  • PII detection and redaction (account IDs, emails)
  • Topic restrictions: no cost advice, no security vulnerability assessment, no infrastructure changes
  • Dangerous phrase detection
  • Post-processing PII scrubbing (defence in depth)
        ↓
Layer 5: Human-in-the-Loop Review Gate
  • Operational briefs: auto-approved (reviewer reads before using)
  • Customer-facing reports: REQUIRE explicit reviewer approval before delivery
  • Leadership summaries: REQUIRE explicit reviewer approval before delivery
  • 48-hour review window with auto-expiry
  • Full audit trail (who approved, when, for which account)
```

---

## Shared Responsibility Model

| Responsibility | MIO Agent | Reviewer / Operator | End User |
|---|---|---|---|
| Accuracy of AWS API data | AWS APIs are the source of truth — MIO Agent reads but cannot guarantee API accuracy | Verify findings against account knowledge | Grant accurate access permissions |
| Finding validation | Automated evidence anchoring + deduplication | Review operational brief before using | — |
| Customer report delivery | Blocks delivery until reviewer approves | **Must review and approve** before sharing | — |
| Acting on recommendations | Generates recommendations only — never acts | Validate recommendations fit account context | Implement approved recommendations |
| Assessment completeness | Clearly states access tier and confidence level | Upgrade to Tier 3 for critical assessments | Grant read-only IAM role for full depth |
| Data security | Read-only access, no data stored beyond TTL | Do not share reports outside approved channels | Control who can grant IAM access |

---

## What MIO Agent Does NOT Do

MIO Agent is explicitly scoped to monitoring and observability assessment only. It will not:

- **Provide cost estimates or pricing** — any cost-related content is blocked by guardrails
- **Assess security vulnerabilities** — CVEs, penetration testing, exploit guidance are outside scope
- **Make infrastructure changes** — MIO Agent is read-only and cannot modify any customer resource
- **Replace human judgement** — reports are advisory inputs for TAM/customer conversations, not decisions
- **Guarantee completeness** — assessment depth depends on access tier; Tier 1 provides indicators, not conclusions
- **Provide compliance assessments** — regulatory compliance (HIPAA, PCI, SOC2) is outside scope

---

## Known Limitations

### 1. Access Tier Limitations
| Tier | What You Get | What's Missing |
|---|---|---|
| Tier 1 (internal signals) | Surface-level indicators from AWS internal tooling | No live account data, no resource-level findings |
| Tier 2 (TAM artifacts) | Analysis of uploaded IaC and exports | No real-time account state, may be outdated |
| Tier 3 (live access) | Full depth analysis | Cannot access application data, secrets, or databases |

### 2. Third-Party Tool Validation Depth
MIO Agent validates *presence and coverage* of third-party tools (Datadog, Dynatrace, etc.) but cannot validate the *quality* of their configuration. An agent may be present on all instances but configured to collect the wrong metrics.

### 3. Dynamic Environments
For highly dynamic environments (frequent Lambda deployments, auto-scaling), the assessment reflects a point-in-time snapshot. A new Lambda deployed 30 seconds after assessment completes will not be included.

### 4. OMS Score Interpretation
The OMS is a relative indicator, not an absolute certification. A score of 4.2 does not mean the customer will never have an incident — it means their monitoring posture is strong relative to the assessed dimensions. Context matters.

### 5. Narrative Hallucination Risk
Despite guardrails, the Narrative Agent (Amazon Bedrock Claude 3.5 Sonnet) may occasionally rephrase findings in ways that add nuance not present in the source data. TAMs should always verify specific claims against the underlying finding evidence before presenting to customers.

---

## Human Review Workflow

```
Assessment completes
        ↓
TAM Brief generated → Auto-approved (TAM reads before using in conversation)
        ↓
Customer Report generated → Status: PENDING_REVIEW
        ↓
TAM receives notification: "Customer report ready for review"
        ↓
TAM reviews report content
        ↓
TAM approves → S3 presigned URL generated → Report shareable with customer
TAM rejects → Rejection reason logged → Report regenerated with feedback
        ↓ (if not reviewed within 48 hours)
Report expires → TAM must re-trigger assessment
```

### Why 48 Hours?
AWS environments change. A report generated Monday may no longer accurately reflect the account by Thursday. The 48-hour window ensures customers receive current information, not stale assessments.

---

## Feedback and Calibration

TAMs can mark individual findings as accurate or inaccurate via the API:

```bash
POST /feedback
{
  "finding_id": "uuid",
  "assessment_id": "mio-...",
  "is_accurate": false,
  "notes": "Customer actually had X-Ray enabled — our SSM check missed it"
}
```

If more than 20% of findings in a dimension are marked inaccurate across assessments, the system logs a calibration alert. This feeds ongoing model and logic improvements.

---

## Audit Trail

Every MIO Agent action is auditable:

| Event | Where Logged |
|---|---|
| Assessment triggered | CloudTrail (Lambda invocation) |
| Customer account accessed | CloudTrail (STS AssumeRole + all API calls with `MIOAgentReadOnlySession`) |
| Report generated | DynamoDB `mio-agent-reviews` table |
| Report approved/rejected | DynamoDB with TAM alias, timestamp, notes |
| Finding feedback | DynamoDB `mio-agent-feedback` table |
| Guardrail violations | CloudWatch Logs (structured JSON) |

---

## Incident Response

If MIO Agent produces an incorrect finding that is presented to a customer and causes harm:

1. TAM marks the finding as inaccurate via the feedback API
2. A corrected assessment can be re-triggered immediately
3. The TAM's manager and the MIO Agent maintainers should be notified
4. The finding pattern is reviewed and guardrails updated if needed

Contact: Open a GitHub issue with label `accuracy-incident`

---

## A Note on Trust

The core design challenge for any AI agent is balancing automation with human oversight — and MIO Agent is built with that tension in mind. It is not designed to replace TAM expertise. It is designed to make that expertise more scalable and evidence-based.

The Jurassic Park principle applies: the agent can identify that distributed tracing is missing on 23 Lambda functions and calculate the impact on MTTD. The TAM decides how to present that to the customer, what to prioritize, and how to frame it in the context of the relationship. That human judgement is irreplaceable.

MIO Agent's job is to ensure the TAM walks into every customer conversation with complete, accurate, evidence-backed information — so the conversation can focus on strategy, not discovery.
