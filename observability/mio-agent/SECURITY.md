# Copyright (c) 2026 Amazon Web Services
# Licensed under the MIT License
# See LICENSE file in the project root for full license information.

# Security — MIO Agent Threat Model

**Generated:** 2026-07-31  
**Method:** STRIDE threat modeling using the official AWS Threat Modeling MCP Server  
**Code revision:** Validated against 89 Python source files  
**Threat Composer export:** [`.threatmodel/threat-model.json`](.threatmodel/threat-model.json) | [`.threatmodel/threat-model.md`](.threatmodel/threat-model.md)

---

## Security Gate Verdict

| Metric | Count |
|---|---|
| Total threats identified | 17 |
| Fully mitigated (`threatResolved`) | 7 |
| Partially mitigated / open (`threatIdentified`) | 10 |
| **Unmitigated HIGH threats** | **4** |
| **Unmitigated CRITICAL threats** | **1** |

> **⚠️ WARN — 4 unmitigated HIGH + 1 unmitigated CRITICAL**  
> All HIGH/CRITICAL threats have partial mitigations and documented remediation paths.  
> No blocker-level unaddressed risks without mitigating controls in place.  
> Recommended improvements listed in [Open Recommendations](#open-recommendations).

---

## STRIDE Risk Summary

| # | STRIDE Category | Threat | Severity | Likelihood | Status | Primary Component |
|---|---|---|---|---|---|---|
| 1 | Spoofing | Forge/replay IAM SigV4 requests to POST /assess or approve reports | HIGH | Possible | ✅ Resolved | API Gateway |
| 2 | Spoofing | Stolen MIOAgentReadOnly ARN used for unauthorized cross-account AssumeRole | HIGH | Unlikely | ✅ Resolved | Coordinator Lambda |
| 3 | Spoofing | TAM alias impersonation in reviewed_by field of report approval | MEDIUM | Possible | ⚠️ Identified | API Handler / Reviews Table |
| 4 | Tampering | Modify DynamoDB findings or overwrite S3 reports to inject false recommendations | HIGH | Possible | ✅ Resolved | Assessments Table / S3 |
| 5 | Tampering | Prompt injection via customer-controlled data (tags, IaC, resource names) | HIGH | Likely | ⚠️ Identified | Coordinator Lambda / Bedrock |
| 6 | Tampering | Malformed SQS messages trigger unintended assessments | MEDIUM | Possible | ✅ Resolved | Assessment Queue |
| 7 | Repudiation | Delete/modify Reviews DynamoDB records to erase approval audit trail | MEDIUM | Possible | ⚠️ Identified | Reviews Table |
| 8 | Repudiation | Delete/overwrite S3 report objects to deny report generation | MEDIUM | Unlikely | ✅ Resolved | Reports S3 Bucket |
| 9 | Information Disclosure | Mass exfiltration of customer account IDs, role ARNs, OMS data from DynamoDB/S3 | HIGH | Possible | ✅ Resolved | Assessments / Accounts Tables |
| 10 | Information Disclosure | Intercept presigned S3 URL to access customer assessment report | MEDIUM | Possible | ⚠️ Identified | Reports S3 Bucket |
| 11 | Information Disclosure | Prompt injection causes Bedrock to disclose system prompt content | HIGH | Possible | ⚠️ Identified | Coordinator Lambda / Bedrock |
| 12 | Information Disclosure | SSRF/IMDS exploit exposes Lambda execution role credentials | HIGH | Unlikely | ⚠️ Identified | All Lambda Functions |
| 13 | Denial of Service | Flood POST /assess to exhaust API throttle, Lambda concurrency, Bedrock quota | HIGH | Likely | ✅ Resolved | API Gateway / Coordinator |
| 14 | Denial of Service | Mass Tier 3 assessments exhaust STS and customer account API rate limits | MEDIUM | Possible | ⚠️ Identified | Coordinator Lambda / STS |
| 15 | Elevation of Privilege | Lambda sandbox escape or supply chain compromise grants full infrastructure access | CRITICAL | Unlikely | ⚠️ Identified | All Lambda Functions |
| 16 | Elevation of Privilege | Wildcard STS AssumeRole exploited for access to unregistered customer accounts | HIGH | Possible | ⚠️ Identified | Coordinator Lambda / STS |
| 17 | Elevation of Privilege | Any TAM approves report for out-of-scope customer (approval bypass) | MEDIUM | Possible | ⚠️ Identified | API Handler / Reviews Table |

---

## Detailed Findings by STRIDE Category

### Spoofing

#### S-1 · HIGH · ✅ Resolved — IAM Credential Replay/Forge
**Threat:** An external attacker obtains TAM credentials and forges/replays IAM SigV4 signed requests to `POST /assess` or `POST /reports/{id}/approve`, triggering unauthorized assessments or fraudulent report approvals.

**Mitigations implemented:**
- All API Gateway endpoints use `AuthorizationType.IAM` (verified in `infrastructure/stacks/mio_agent_stack.py`)
- API throttling: 10 req/s sustained / 20 burst (CDK `StageOptions`)
- Least-privilege Lambda execution role (no cross-service write escalation)

---

#### S-2 · HIGH · ✅ Resolved — Cross-Account Role ARN Theft
**Threat:** Attacker who exfiltrates a `MIOAgentReadOnly` role ARN from the Accounts DynamoDB table uses it to directly call `sts:AssumeRole` and gain unauthorized read access to a customer AWS account.

**Mitigations implemented:**
- STS AssumeRole IAM policy scoped to `arn:aws:iam::*:role/MIOAgentReadOnly` only
- Account registration gate (accounts must be registered in `mio-agent-accounts` table)
- `_verify_account_registered()` called at the top of `run_assessment()` before any tier logic — fails closed if the accounts table is unreachable, returns HTTP 403 for unregistered accounts (`orchestrator.py:265`, wired at `:82`, regex `^\d{12}$` validation)

**Fix implemented (v0.1.7):** Runtime account allowlist check added to `coordinator/orchestrator.py`. Called before any tier logic or STS call — fails closed if accounts table is unreachable. Returns HTTP 403 via `api_handler.py` for unregistered accounts. Resolves this threat.

---

#### S-3 · MEDIUM · ⚠️ Identified — TAM Alias Impersonation in Review Approval
**Threat:** A rogue TAM calls `POST /reports/{id}/approve` and supplies a different TAM's alias in the `reviewed_by` field, falsely attributing report approval.

**Mitigations implemented:**
- IAM SigV4 confirms caller identity at API Gateway
- Structured audit logging records all approval events

**Residual risk:** `reviewed_by` is taken from the request body (`body.get("reviewed_by", "unknown-tam")`) rather than derived from the authenticated IAM principal. The audit trail records the claimed alias, not the verified identity.

**Recommended fix:** Extract the IAM caller identity from the Lambda context (`requestContext.authorizer.iam.userId`) and use it as `reviewed_by` instead of the body-supplied value.

---

### Tampering

#### T-1 · HIGH · ✅ Resolved — DynamoDB/S3 Data Manipulation
**Threat:** Attacker with data store write access modifies OMS scores in DynamoDB or overwrites S3 report objects with false recommendations.

**Mitigations implemented:**
- IAM write access scoped to Lambda execution role only (no direct user access)
- DynamoDB PITR enabled on all 4 tables for point-in-time recovery
- S3 versioning preserves previous report versions
- S3 SSL enforcement and AES256 encryption

---

#### T-2 · HIGH · ⚠️ Identified — Prompt Injection via Customer Data
**Threat:** Adversary plants prompt injection payloads in customer-controlled data (resource names, tags, CloudFormation templates) that the agent reads and passes to Amazon Bedrock, causing the LLM to generate false findings or disclose internal configuration.

**Mitigations implemented:**
- 9-pattern regex detection in `input_validator.py` (`_PROMPT_INJECTION_PATTERNS`)
- `sanitize_narrative_input()` applied before Bedrock calls in `narrative.py`
- Amazon Bedrock Guardrails (Layer 4 of guardrail pipeline) for content filtering
- 5-layer guardrail pipeline applied to every assessment output

**Residual risk:** Regex-based prompt injection detection cannot cover all novel jailbreak patterns. Guardrails provide a second layer but are also not guaranteed to catch adversarial LLM prompts in structured data contexts (e.g., JSON field values, YAML tags).

**Recommended fix:** Add structured output schema validation on Bedrock responses (validate JSON schema before processing). Consider using a dedicated LLM safety service or additional output filtering layer.

---

#### T-3 · MEDIUM · ✅ Resolved — SQS Message Injection
**Threat:** Attacker injects malformed messages into the assessment SQS queue to trigger unintended assessments or Lambda errors.

**Mitigations implemented:**
- SQS queue restricted to Lambda IAM role only (no public send permission)
- DLQ after 3 failed receive attempts (14-day retention)
- SQS-managed encryption

---

### Repudiation

#### R-1 · MEDIUM · ⚠️ Identified — Review Audit Record Deletion
**Threat:** A rogue TAM with DynamoDB access deletes or modifies review records in `mio-agent-reviews` table to destroy evidence of report approvals.

**Mitigations implemented:**
- DynamoDB PITR enables recovery of deleted records
- Structured CloudWatch audit logs record all review events
- Human review gate requires explicit TAM approval (creates audit record at approval time)

**Residual risk:** No immutable audit log (e.g., CloudTrail data events) currently configured for DynamoDB item-level operations.

**Recommended fix:** Enable CloudTrail data events for DynamoDB `mio-agent-reviews` table to create an independent, tamper-resistant audit trail.

---

#### R-2 · MEDIUM · ✅ Resolved — S3 Report Deletion Denial
**Threat:** Malicious insider deletes S3 report objects to deny having generated a specific assessment outcome.

**Mitigations implemented:**
- S3 versioning preserves deleted/overwritten objects
- S3 access logs available for reconstruction
- Structured logging records report generation events

---

### Information Disclosure

#### I-1 · HIGH · ✅ Resolved — Mass Data Exfiltration from Data Stores
**Threat:** Attacker with overly broad IAM access reads all customer account IDs, role ARNs, OMS scores, and findings JSON from DynamoDB tables or enumerates all reports from S3.

**Mitigations implemented:**
- Lambda IAM role scoped to specific table ARNs (no scan access on unrelated resources)
- DynamoDB tables not publicly accessible
- S3 bucket with `BlockPublicAccess.BLOCK_ALL`
- All data encrypted at rest (AWS-managed encryption on DynamoDB, AES256 on S3)

---

#### I-2 · MEDIUM · ⚠️ Identified — Presigned URL Interception
**Threat:** A presigned S3 URL for a customer report is intercepted in transit or forwarded to an unintended recipient, giving unauthenticated access to confidential assessment content.

**Mitigations implemented:**
- Default URL expiry: 1 hour (3600 seconds)
- Reports bucket enforces SSL (HTTP requests rejected)

**Recommended fix:** Reduce presigned URL expiry from 3600s to 900s (15 minutes) in `get_report_url()` default. Add URL access logging to detect anomalous accesses.

---

#### I-3 · HIGH · ⚠️ Identified — System Prompt Disclosure via Prompt Injection
**Threat:** Successful prompt injection causes Amazon Bedrock to include the agent's system prompt, guardrail logic, or internal tool names in the generated report output.

**Mitigations implemented:**
- Prompt injection sanitization in `input_validator.py`
- Amazon Bedrock Guardrails applied on all LLM invocations
- Human review gate ensures TAM reviews output before customer delivery

**Residual risk:** If injection bypasses regex sanitization and Guardrails, the system prompt could appear in the TAM-visible output. The human review gate provides a final catch before customer delivery.

---

#### I-4 · HIGH · ⚠️ Identified — Lambda Credential Exposure via SSRF/IMDS
**Threat:** An SSRF vulnerability or exposed IMDS endpoint allows extraction of Lambda execution role temporary credentials, granting full access to all agent data stores and cross-account role assumption capability.

**Mitigations implemented:**
- Lambda runs in managed AWS environment (IMDS access restricted by default)
- Error handlers catch `ClientError` and return generic error responses (no stack traces in API responses)
- Least-privilege IAM role limits blast radius

**Residual risk:** No explicit IMDS token-required mode (`IMDSv2`) enforced in Lambda configuration. No outbound network filtering to block SSRF to internal metadata endpoints.

**Recommended fix:** Enforce `IMDSv2` only on all Lambda functions. Add `aws:SecureTransport` conditions and review outbound network access patterns in VPC configuration.

---

### Denial of Service

#### D-1 · HIGH · ✅ Resolved — API/Bedrock Quota Exhaustion
**Threat:** Attacker floods `POST /assess` to exhaust API Gateway throttle limits, Lambda concurrency, or Bedrock model invocation TPM quotas.

**Mitigations implemented:**
- API Gateway throttling: 10 req/s sustained / 20 burst
- Lambda `reserved_concurrent_executions=10` on Coordinator
- SQS queue decouples inbound requests from processing

---

#### D-2 · MEDIUM · ⚠️ Identified — STS/Customer Account API Rate Exhaustion
**Threat:** Mass concurrent Tier 3 assessments exhaust STS `AssumeRole` API rate limits or AWS service API call quotas in customer accounts.

**Mitigations implemented:**
- API Gateway throttling limits inbound assessment rate
- Lambda concurrency limit bounds parallel Coordinator executions

**Residual risk:** No per-account rate limiting — a single account could receive many concurrent assessment triggers from different sources (scheduler + EventBridge + on-demand simultaneously).

---

### Elevation of Privilege

#### E-1 · CRITICAL · ⚠️ Identified — Lambda Sandbox Escape / Supply Chain
**Threat:** Critical vulnerability in Python runtime, `boto3`, or a Lambda dependency enables sandbox escape or supply chain compromise, granting access to Lambda execution credentials and full infrastructure access.

**Mitigations implemented:**
- All dependencies pinned to exact versions in `requirements.txt` (e.g., `urllib3==2.5.0`)
- Python 3.12 runtime (current LTS with active security patches)
- Least-privilege IAM role limits blast radius of credential theft

**Residual risk:** No hash-pinning (`pip --require-hashes`) in production build. No SBOM generation or automated vulnerability scanning in CI pipeline (beyond Probe scans at repo level).

**Recommended fix:** Add `pip --require-hashes` to deployment build. Integrate automated dependency vulnerability scanning (e.g., `pip-audit`) in CI pipeline pre-deploy step.

---

#### E-2 · HIGH · ⚠️ Identified — Wildcard STS AssumeRole for Unregistered Accounts
**Threat:** Attacker exploits the wildcard account ID in `arn:aws:iam::*:role/MIOAgentReadOnly` to call `sts:AssumeRole` for customer accounts not registered in the `mio-agent-accounts` table, or pivots to higher-privilege roles via a misconfigured customer trust policy.

**Mitigations implemented:**
- STS IAM policy restricts role name to `MIOAgentReadOnly` pattern
- Account registration required before scheduled assessments

**Fix implemented (v0.1.7):** `_verify_account_registered()` added to `coordinator/orchestrator.py`. Called at the top of `run_assessment()` before any tier logic — fails closed if the accounts table is unreachable. Returns HTTP 403 via `api_handler.py` for unregistered accounts. Resolves this threat.

---

#### E-3 · MEDIUM · ⚠️ Identified — Report Approval Scope Bypass
**Threat:** Any TAM with valid IAM credentials can call `POST /reports/{id}/approve` for any report ID, including reports for customers outside their assigned scope.

**Mitigations implemented:**
- IAM SigV4 authentication confirms caller is a valid TAM
- Human review gate ensures a human reviews before delivery
- Audit log records which TAM approved each report

**Residual risk:** No customer-to-TAM assignment enforcement in the approval API. Any authenticated TAM can approve any report.

---

## Open Recommendations

| Priority | Recommendation | Affected Threat(s) | Effort |
|---|---|---|---|
| ✅ DONE | Account allowlist check before `sts.assume_role()` — implemented in `_verify_account_registered()` | S-2, E-2 | Low |
| HIGH | Derive `reviewed_by` from IAM caller identity, not request body | S-3 | Low |
| HIGH | Enforce `IMDSv2` on Lambda functions; review outbound network SSRF surface | I-4 | Low |
| MEDIUM | Reduce presigned S3 URL expiry from 3600s to 900s | I-2 | Low |
| MEDIUM | Enable CloudTrail data events on DynamoDB `mio-agent-reviews` and S3 reports bucket | R-1, T-1 | Low |
| MEDIUM | Add CloudWatch alarms: Lambda error rate >5%, Bedrock throttle >10/min, DLQ >0 | D-1, D-2 | Low |
| MEDIUM | Add `pip --require-hashes` and `pip-audit` to CI/CD pipeline | E-1 | Medium |
| LOW | Add customer-to-TAM scoping on report approval endpoint | E-3 | Medium |

---

## Implemented Security Controls Summary

| Control | Location | Status |
|---|---|---|
| IAM SigV4 on all API endpoints | `infrastructure/stacks/mio_agent_stack.py` | ✅ Implemented |
| API throttling (10 req/s / 20 burst) | CDK `StageOptions` | ✅ Implemented |
| 5-layer guardrail pipeline | `src/mio_agent/guardrails/pipeline.py` | ✅ Implemented |
| Prompt injection regex (9 patterns) | `src/mio_agent/guardrails/input_validator.py` | ✅ Implemented |
| Amazon Bedrock Guardrails (content + PII) | `src/mio_agent/guardrails/bedrock_guardrails.py` | ✅ Implemented |
| Human-in-the-loop review gate | `src/mio_agent/guardrails/human_review.py` | ✅ Implemented |
| Confidence gate (tier-based OMS caps) | `src/mio_agent/guardrails/confidence_gate.py` | ✅ Implemented |
| Least-privilege IAM (scoped ARNs) | `infrastructure/stacks/mio_agent_stack.py` | ✅ Implemented |
| STS AssumeRole scope (role name only) | CDK IAM PolicyStatement | ✅ Implemented |
| DynamoDB PITR (all 4 tables) | CDK DynamoDB Table definitions | ✅ Implemented |
| DynamoDB AWS-managed encryption | CDK DynamoDB Table definitions | ✅ Implemented |
| S3 block public access + SSL enforce | CDK S3 Bucket | ✅ Implemented |
| S3 AES256 encryption + versioning | CDK S3 Bucket | ✅ Implemented |
| SQS encryption + DLQ (3 attempts) | CDK SQS Queue | ✅ Implemented |
| Lambda reserved concurrency (10) | CDK Lambda Function | ✅ Implemented |
| Structured JSON logging (30-day retention) | `src/mio_agent/utils/logger.py` + CDK | ✅ Implemented |
| Dependency pinning (exact versions) | `requirements.txt` | ✅ Implemented |
| Input size limits (5MB IaC, 20 keys) | `src/mio_agent/guardrails/input_validator.py` | ✅ Implemented |

---

## Threat Actors

| Actor | Type | Capability | Relevance |
|---|---|---|---|
| External Attacker | External | High | API abuse, credential theft |
| Malicious Insider (Rogue TAM) | Insider | High | Data exfiltration, approval fraud |
| Prompt Injection Attacker | External | Medium | LLM manipulation via customer data |
| Supply Chain Attacker | External | High | Dependency compromise |
| Compromised Customer Account | External | Medium | Adversarial data in assessed environment |
| Nation-State Actor | Nation-state | High | Intelligence gathering across customer accounts |

---

## Data Classification

| Asset | Classification | Sensitivity |
|---|---|---|
| Customer Account IDs + IAM Role ARNs | Confidential | 5/5 |
| Assessment Findings (OMS, gaps) | Confidential | 5/5 |
| Cross-Account IAM Role ARN | Internal | 5/5 |
| Lambda Execution Credentials | Internal | 5/5 |
| Generated Assessment Reports | Confidential | 4/5 |
| Bedrock System Prompts | Internal | 3/5 |
| Review Audit Records | Internal | 3/5 |
| Presigned S3 Report URLs | Internal | 3/5 |

---

## References

- Threat model source: [`.threatmodel/threat-model.json`](.threatmodel/threat-model.json) (AWS Threat Composer compatible)
- Responsible AI posture: [`RESPONSIBLE_AI.md`](RESPONSIBLE_AI.md)
- IAM permission boundary: documented in project security policies
- Guardrail architecture: [`src/mio_agent/guardrails/`](src/mio_agent/guardrails/)
