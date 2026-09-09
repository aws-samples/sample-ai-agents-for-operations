# Security Threat Model — DART Agent

A STRIDE threat model for the DART sample agent, covering `src/`, the CDK infrastructure
(`infra/lib/DartAgentStack.ts`), the IAM policies, the `Dockerfile`, and the tool definitions.
The machine-readable export lives at `.threatmodel/dart-agent.threatmodel.json`.

> **This is sample code.** Review the threats and mitigations below and complete your own
> security review before deploying in production. Items marked *deployer responsibility* are
> configuration/hardening choices left to you.

## System Overview

DART (Dataset Audit & Readiness for Training) performs pre-flight validation of LLM fine-tuning
datasets. The reference deployment runs on Amazon ECS Fargate in a private subnet, uses Amazon
Bedrock for the report narrative and Amazon Comprehend for PII detection. Outputs are written to
a KMS-encrypted Amazon S3 bucket and a DynamoDB audit table.

**Data classification:** Training datasets — may contain PII and confidential content.

**Security posture built in:**
- Least-privilege IAM task role — read-only on customer data, write scoped to agent-managed resources
- Customer-managed KMS key with rotation; encryption at rest on S3, DynamoDB, and CloudWatch Logs
- S3 bucket: public access blocked, versioned, TLS enforced
- DynamoDB: customer-managed SSE, point-in-time recovery, TTL
- VPC private subnets with S3/DynamoDB gateway endpoints; single NAT for egress
- Non-root container user; digest-pinned base image; health check
- Input-path validation on every tool that accepts a path (traversal + allowlist guards)
- Per-run audit record written to DynamoDB
- The agent never sends raw training data externally and never mutates the original dataset

## Risk Summary

| STRIDE Category | HIGH | MEDIUM | LOW | Total |
|---|---|---|---|---|
| Spoofing | 0 | 1 | 1 | 2 |
| Tampering | 1 (resolved) | 1 | 1 | 3 |
| Repudiation | 0 | 0 | 1 | 1 |
| Information Disclosure | 1 | 1 | 1 | 3 |
| Denial of Service | 0 | 1 | 1 | 2 |
| Elevation of Privilege | 0 | 1 | 1 | 2 |
| **Total** | **2** | **5** | **6** | **13** |

**Unmitigated HIGH threats:** 0. T-1 (Tampering) is resolved via input-path validation. I-1
(Information Disclosure) is partially mitigated — no raw PII values are emitted; content stays in
the deployer's AWS account; the residual is record-locator metadata in agent-managed S3/CloudWatch.

## Detailed Findings

### Spoofing

- **[MEDIUM] No authentication layer on the invocation entrypoint** — Component: `src/dart-agent/agent.py` (`handle_request`) — The handler accepts a `message` and the CDK stack defines no ingress auth (no ALB/API Gateway/Cognito). **Mitigation (deployer responsibility):** front the agent with authenticated ingress (Amazon Cognito / IAM auth / API Gateway) before production.
- **[LOW] Caller-supplied `run_id`** — Component: `agent.py` — A forged/colliding `run_id` could pollute audit records. **Mitigation:** `run_id` is generated server-side; any caller-supplied value is ignored. *(Resolved.)*

### Tampering

- **[HIGH] ~~`dataset_path` not validated (path traversal / arbitrary read)~~ — RESOLVED** — Component: `tools/_validation.py`, wired into `profile_dataset`, `detect_duplicates`, `scan_pii`, `apply_safe_fixes`, `check_training_viability` — All caller-supplied dataset/output paths are validated before any S3 or filesystem access. S3 URIs are checked for well-formed bucket names, blocked from traversal segments, and matched against optional `ALLOWED_S3_BUCKETS`/`ALLOWED_S3_PREFIXES` allowlists; local paths are resolved and (when `ALLOWED_LOCAL_BASE_DIR` is set) confined to that directory. Rejections raise `ValueError` before the read.
- **[MEDIUM] Prompt injection via dataset-derived content** — Component: `tools/generate_preflight_report.py` — Finding titles/details derived from dataset content are embedded in the Bedrock prompt. **Mitigation:** findings pass through `json.dumps`; avoid interpolating raw record values; deployers handling untrusted content should attach an Amazon Bedrock Guardrail.
- **[LOW] ~~Docker base image not pinned~~ — RESOLVED** — Component: `Dockerfile` — Base image sourced from AWS Public ECR (`public.ecr.aws/docker/library/python:3.12-slim`) and pinned by digest; `apt-get clean` added; `HEALTHCHECK` in JSON/exec form. For internal production deployments, use an internal ECR image per your organization's guidance.

### Repudiation

- **[LOW] ~~Audit trail not persisted~~ — RESOLVED** — Component: `src/dart-agent/audit.py`, `agent.py` — Each run writes a durable record (`run_id`, `timestamp`, status, TTL) to the DynamoDB audit table on both success and error paths.

### Information Disclosure

- **[HIGH] PII/affected-record locators in tool output** — Component: `tools/scan_pii.py`, `generate_preflight_report.py` — Raw PII values are never returned, but per-record indices, columns, and entity types are written to the S3 output bucket and CloudWatch. If bucket/log access is broader than the data owner, this leaks the location/shape of sensitive records. **Mitigation (deployer responsibility):** restrict S3 output bucket and CloudWatch access to the data owner; minimize persisted record-level locators; enable Amazon Bedrock Guardrails PII handling. *(Partially mitigated — no raw PII values emitted; content stays in the AWS account.)*
- **[MEDIUM] ~~Downloaded datasets left in `/tmp`~~ — RESOLVED** — Component: `tools/profile_dataset.py` — Downloads use `tempfile.mkdtemp()` (unpredictable, 0700-permission dir) and are deleted in a `finally` block after processing.
- **[LOW] ~~Raw exception strings returned to caller~~ — RESOLVED** — Component: `agent.py` — Callers receive a generic message + correlation id; full detail is logged server-side only.

### Denial of Service

- **[MEDIUM] Unbounded dataset size** — Component: `profile_dataset.py`, `detect_duplicates.py`, `scan_pii.py` — No size/record-count limits before loading datasets into memory; a large/adversarial dataset can exhaust the task and generate large Amazon Comprehend spend. **Mitigation (deployer responsibility):** enforce max file size and record count; stream/sample; set task timeouts and cost guardrails. PII detection uses the batch API (`BatchDetectPiiEntities`, up to 25 docs/call).
- **[LOW] No retry/backoff on transient AWS errors** — Component: `generate_preflight_report.py`, `scan_pii.py` — **Mitigation:** enable botocore adaptive retries.

### Elevation of Privilege

- **[MEDIUM] `Resource: "*"` on Comprehend and X-Ray** — Component: `infra/lib/DartAgentStack.ts` — Comprehend and X-Ray do not support resource-level scoping for these actions (documented AWS limitation). *(Mitigated — the role has no mutating actions on customer resources and Bedrock is scoped to explicit model ARNs.)*
- **[LOW] ~~Tools can write to caller-controlled `output_path`~~ — RESOLVED** — Component: `tools/apply_safe_fixes.py`, `tools/scan_pii.py` — `output_path` is validated and confined to `ALLOWED_LOCAL_BASE_DIR` when set.

## Key Management & Data Handling

- **Encryption at rest:** customer-managed KMS key (rotation enabled) across S3, DynamoDB, CloudWatch Logs.
- **Encryption in transit:** S3 `enforceSSL`; AWS SDK TLS to Amazon Bedrock/Comprehend.
- **Secrets:** loaded via environment variables / AWS Secrets Manager — none hardcoded.
- **Data residency:** dataset content stays within your AWS account boundary; no third-party egress.
- **Data minimization:** original datasets are never modified; redaction/fixes produce new files.

## Deployer Hardening Checklist

Before production use, review and apply as appropriate:

1. **Add authenticated ingress** in front of the agent (Cognito / IAM auth / API Gateway).
2. **Set path allowlists** — `ALLOWED_S3_BUCKETS`, `ALLOWED_S3_PREFIXES`, `ALLOWED_LOCAL_BASE_DIR` (default empty; traversal guards always run).
3. **Scope access to outputs** — restrict the S3 output bucket and CloudWatch logs to the data owner; minimize persisted PII locators.
4. **Attach an Amazon Bedrock Guardrail** if processing untrusted dataset content (PII handling + prompt-injection defense).
5. **Enforce input limits** — max file size / record count, task timeouts, and Comprehend cost guardrails.
6. **Wire your own CI security scans** (e.g. dependency audit, container scan, IaC checks) and run the included test suites (`make test-all`).
7. **Use an internal/base image appropriate to your org** for production instead of the public sample base image.
