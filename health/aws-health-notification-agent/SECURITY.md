# AWS Health Notification Classifier — Threat Model

## Introduction

### Purpose

This document provides the threat model for the AWS Health Notification Classifier project. The system classifies AWS Health Dashboard notifications into actionable categories (BREAKING_CHANGE, COST_IMPLICATION, SECURITY_RELATED), performs impact analysis, creates Jira tickets, and optionally executes approved remediation actions (e.g., EKS cluster upgrades).

This asset is a proof-of-concept / demo intended to demonstrate AI-powered health event triage using Amazon Bedrock AgentCore Runtime with Strands Agents.

### Project/Asset Overview

**Major components:**
- **aha_eventbridge_lambda** — Lambda function that routes EventBridge health events to the AgentCore agent or directly to SNS
- **phd_notification_classifier** — Strands Agent running on Amazon Bedrock AgentCore Runtime (ARM64 container) that classifies events, performs impact analysis, and publishes results
- **approval_lambda** — Lambda handling human-approval flow for remediation actions (two-step: GET confirmation page, POST executes)
- **routing_config_lambda** — Lambda triggered by S3 uploads, invokes Amazon Bedrock to parse routing documents
- **routing_approval_lambda** — Lambda handling Slack interactive payloads for routing config approval

**AWS services used:** Lambda, EventBridge, SNS, SQS, DynamoDB, S3, API Gateway (HTTP API), SES, Secrets Manager, IAM, Amazon Bedrock, Amazon Bedrock AgentCore Runtime, AWS Organizations, Amazon EKS

**3rd party libraries/services:**
- `strands-agents` — Agent framework
- `bedrock-agentcore` — AgentCore Runtime SDK
- Anthropic Claude Sonnet (via Amazon Bedrock) — LLM for classification and routing config parsing
- Jira REST API (Atlassian) — Ticket creation
- Slack Webhooks — Notifications and interactive approvals
- AWS EKS MCP Server (`awslabs.eks-mcp-server`) — EKS cluster insights via MCP

**Build & Deploy:** CloudFormation (SAM transform, no SAM CLI required) for Lambda stack, Docker (ARM64) for AgentCore container, ECR for image storage. Single `deploy.sh` script.

### Assumptions

| ID | Assumption | Comments |
|---|---|---|
| A-01 | This asset is deployed in a non-production/demo environment for educational purposes | Production hardening (WAF, VPC, enhanced monitoring) would be required for production use |
| A-02 | TLS 1.2+ is enforced on all AWS API calls | AWS SDK default behavior; S3 bucket policy explicitly denies non-TLS |
| A-03 | The AWS account running this solution is not shared with untrusted principals | IAM policies assume single-tenant account ownership |
| A-04 | SES is in sandbox mode — both sender and recipient must be verified | Limits blast radius of email-based attacks |
| A-05 | The EKS MCP Server is an AWS-managed preview service and is trusted | Security posture inherits from AWS service controls |
| A-06 | AWS Health event payloads from EventBridge are trusted AWS-originated data | Events come from `aws.health` source on the default bus or `aha` on custom bus |
| A-07 | Secrets Manager rotation is handled operationally outside this codebase | Jira API token rotation is a manual operational process |

### References

- **Project Team:** AWS Solutions Team
- **CSR Link:** N/A (internal demo)
- **SFDC Opportunity Link:** N/A
- **Architecture Diagram:** `assets/PHD_Agent.png`

## Solution Architecture

### Architecture Diagram

See `assets/PHD_Agent.png` for the visual diagram.

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│  AWS Health /   │────▶│  EventBridge         │────▶│  aha-eventbridge-lambda │
│  AHA (aha-eb01) │     │  (default + custom)  │     │  (HealthEventFunction)  │
└─────────────────┘     └──────────────────────┘     └───────────┬─────────────┘
                                                                  │
                                              ┌───────────────────┼───────────────────┐
                                              ▼                   ▼                   ▼
                                    ┌──────────────┐   ┌──────────────────┐   ┌─────────────┐
                                    │ AgentCore    │   │ SNS Topic        │   │ DynamoDB    │
                                    │ Runtime      │   │ (notifications)  │   │ (approvals) │
                                    │ (Strands)    │   └──────────────────┘   └──────┬──────┘
                                    └──────┬───────┘                                  │
                                           │                                          ▼
                                           ▼                              ┌───────────────────┐
                                    ┌──────────────┐                      │ API Gateway       │
                                    │ Amazon       │                      │ /approve (GET/POST)│
                                    │ Bedrock      │                      └─────────┬─────────┘
                                    └──────────────┘                                │
                                                                                    ▼
                                                                          ┌──────────────────┐
                                                                          │ Approval Lambda  │
                                                                          │ → AgentCore      │
                                                                          │   (remediation)  │
                                                                          └──────────────────┘

┌───────────────┐     ┌──────────────────┐     ┌─────────────────────┐     ┌──────────────┐
│ S3 Upload     │────▶│ routing-config-  │────▶│ Slack Webhook       │────▶│ routing-     │
│ (routing doc) │     │ lambda           │     │ (approval request)  │     │ approval-    │
└───────────────┘     └──────────────────┘     └─────────────────────┘     │ lambda       │
                              │                                             └──────┬───────┘
                              ▼                                                    ▼
                      ┌──────────────┐                                    ┌──────────────────┐
                      │ Amazon       │                                    │ Secrets Manager  │
                      │ Bedrock      │                                    │ (routing config) │
                      └──────────────┘                                    └──────────────────┘
```

### Main Functionality / Use Cases

1. **Health Event Classification** — Receive AWS Health events via EventBridge, invoke AgentCore agent to classify as BREAKING_CHANGE / COST_IMPLICATION / SECURITY_RELATED, publish structured results to SNS
2. **Impact Analysis** — For BREAKING_CHANGE events, analyze affected resources (EKS clusters, Keyspaces trust stores) and determine confirmed/unconfirmed impact
3. **Jira Ticket Creation** — Create Jira tickets routed to the appropriate team based on service/OU/account mappings
4. **Human-Approved Remediation** — For confirmed-impact events, send SES email with approval link; on approval, execute remediation (e.g., EKS cluster upgrade) via AgentCore
5. **Routing Config Management** — Upload routing documents to S3, parse with Amazon Bedrock, approve via Slack, persist to Secrets Manager

### APIs

| API | Method | Callable from Internet | Authorized Callers | Comments |
|---|---|---|---|---|
| `/approve` (GET) | GET | Yes | Anyone with valid token | Shows confirmation page; token validated against DynamoDB |
| `/approve` (POST) | POST | Yes | Anyone with valid token | Executes remediation; token must be pending + not expired |
| `/slack/interactive` (POST) | POST | Yes | Slack (signature verified) | Handles routing approval interactive payloads |
| `/approve-routing` (GET) | GET | Yes | Anyone with valid token | Shows routing approval confirmation page |
| `/approve-routing` (POST) | POST | Yes | Anyone with valid token | Applies routing config to Secrets Manager |

## Assets / Dependencies

| Asset Name | Asset Usage | Data Type | Comments |
|---|---|---|---|
| Customer-managed AWS KMS key | Encrypting DynamoDB, SNS, SQS at rest | Service data-at-rest | Single key, auto-rotated annually |
| DynamoDB ApprovalStore | Stores approval tokens + remediation payloads | RESTRICTED | TTL 7 days, AWS KMS-encrypted, PITR enabled |
| SNS Topic | Publishes classified notification summaries | CONFIDENTIAL | AWS KMS-encrypted, contains account IDs |
| SQS Dead Letter Queue | Failed Lambda invocations | CONFIDENTIAL | AWS KMS-encrypted |
| S3 RoutingConfigBucket | Stores uploaded routing documents | INTERNAL | SSE-S3, versioned, public access blocked, TLS enforced |
| Secrets Manager (Jira) | Jira API token + team routing mappings | RESTRICTED | AWS-managed key, no auto-rotation configured |
| Slack Signing Secret | HMAC verification of Slack payloads | RESTRICTED | Passed as Lambda env var (NoEcho parameter) |
| SES Identity | Sends approval emails | INTERNAL | Sandbox mode, verified sender/recipient |
| TLS (AWS SDK default) | All API calls encrypted in transit | N/A | TLS 1.2+ enforced by AWS SDKs |
| Anthropic Claude (Amazon Bedrock) | LLM for classification + routing parsing | CONFIDENTIAL | Data not used for training (Amazon Bedrock guarantee) |
| EKS MCP Server | Cluster insights, upgrade readiness | CONFIDENTIAL | AWS-managed preview service, `--allow-write` flag |

## Threats & Mitigations

### Threat Actors

| Actor # | Threat Actor | Description |
|---|---|---|
| TA1 | Internet attacker | Unauthenticated actor who discovers approval URLs or API endpoints |
| TA2 | Compromised email recipient | Actor with access to the notification recipient's email inbox |
| TA3 | Malicious S3 uploader | Actor with S3 PutObject permission to the routing config bucket |
| TA4 | Prompt injection via health event | Attacker who can influence health event description content |
| TA5 | Compromised AWS credentials | Actor with stolen IAM credentials for the deployment account |

### Threat & Mitigation Detail

| Threat # | Priority | Threat | STRIDE | Affected Assets | Mitigations | Decision | Status |
|---|---|---|---|---|---|---|---|
| T-001 | High | TA1 attempts to brute-force approval tokens to trigger unauthorized remediation | Spoofing | DynamoDB ApprovalStore | M-001, M-002 | Mitigate | Implemented |
| T-002 | High | TA2 replays a stolen approval URL to execute remediation after legitimate use | Repudiation | DynamoDB ApprovalStore | M-003, M-004 | Mitigate | Implemented |
| T-003 | High | TA2 triggers remediation by clicking approval link (email pre-fetch) | Tampering | Approval API | M-005 | Mitigate | Implemented |
| T-004 | High | TA4 injects malicious instructions in health event description to manipulate agent behavior | Tampering | AgentCore Runtime, Amazon Bedrock | M-006, M-007, M-017 | Mitigate | Partial |
| T-005 | Medium | TA3 uploads malicious routing document to redirect tickets to attacker-controlled team | Tampering | S3 RoutingConfigBucket, Secrets Manager | M-008, M-009 | Mitigate | Implemented |
| T-006 | Medium | TA1 spoofs Slack interactive payload to approve routing without authorization | Spoofing | Routing Approval Lambda | M-010 | Mitigate | Implemented |
| T-007 | Medium | TA1 intercepts SNS notification data containing account IDs and resource details | Information Disclosure | SNS Topic | M-011 | Mitigate | Implemented |
| T-008 | Medium | TA5 escalates privileges via overly broad IAM policies | Elevation of Privilege | IAM Roles | M-012 | Mitigate | Implemented |
| T-009 | Low | TA1 causes denial of service by flooding approval API with invalid tokens | Denial of Service | API Gateway, DynamoDB | M-013 | Accept | API Gateway default throttling |
| T-010 | Medium | TA4 causes LLM to leak sensitive data from system prompt or tool outputs | Information Disclosure | AgentCore Runtime | M-007, M-014 | Mitigate | Partial |
| T-011 | Medium | Burst of health events floods downstream channels (Slack, Jira, SNS/email) with excessive notifications | Denial of Service | Slack Webhooks, Jira REST API, SNS Topic | M-018, M-019, M-020 | Accept | Partially mitigated; see Notification Flooding section |

### OWASP Top 10 for LLM Applications

| OWASP LLM # | Risk | Applicability | Mitigation |
|---|---|---|---|
| LLM01 | Prompt Injection | **Applicable** — Health event descriptions are external input | M-006: Structured system prompt with clear delimiters; M-007: Output schema validation |
| LLM02 | Insecure Output Handling | **Applicable** — Agent output drives remediation actions | M-007: JSON schema validation before processing; M-005: Human approval gate |
| LLM03 | Training Data Poisoning | **Not applicable** — Using managed Amazon Bedrock model, no fine-tuning | N/A |
| LLM04 | Model Denial of Service | **Low risk** — Amazon Bedrock manages rate limiting and availability | API Gateway throttling, Lambda timeout (900s) |
| LLM05 | Supply Chain Vulnerabilities | **Applicable** — EKS MCP Server is a 3rd party dependency | M-015: AWS-managed MCP server, pinned version |
| LLM06 | Sensitive Information Disclosure | **Applicable** — System prompt contains classification rules | M-014: No secrets in system prompt; tool outputs filtered |
| LLM07 | Insecure Plugin Design | **Applicable** — Agent has tools that can modify infrastructure (if IAM is explicitly granted) | M-016: Tools require explicit human approval for destructive actions; M-017: IAM denies mutations by default |
| LLM08 | Excessive Agency | **Mitigated** — Agent code includes `upgrade_eks_cluster` but IAM denies it by default | M-005: Two-step human approval; M-016: Remediation gated behind token; M-017: IAM default-deny |
| LLM09 | Overreliance | **Low risk** — Classification results are published for human review | SNS notifications include full reasoning |
| LLM10 | Model Theft | **Not applicable** — Using managed Amazon Bedrock service | N/A |

## Mitigations

| Mitigation # | Mitigation Description | Threats Mitigating | Status | Comments |
|---|---|---|---|---|
| M-001 | Approval tokens use `secrets.token_urlsafe(48)` (384 bits of entropy) making brute-force infeasible | T-001 | Implemented | ~2⁃⁸⁴ possible values |
| M-002 | Tokens expire after 7 days via DynamoDB TTL | T-001 | Implemented | `expires_at` attribute |
| M-003 | Single-use tokens: DynamoDB conditional update sets status to "approved" atomically | T-002 | Implemented | `ConditionExpression` prevents reuse |
| M-004 | Token status checked before execution — only "pending" tokens are valid | T-002 | Implemented | Rejects expired, approved, or unknown tokens |
| M-005 | Two-step approval: GET shows confirmation page, POST executes action | T-003 | Implemented | Prevents email scanner pre-fetch from triggering actions |
| M-006 | Structured system prompt with explicit delimiters separating instructions from user data | T-004 | Implemented | Health event data injected after clear boundary markers |
| M-007 | Agent output validated against expected JSON schema before processing | T-004, T-010 | Implemented | `summary_formatter.py` parses and validates structure |
| M-008 | Routing config changes require Slack approval workflow before persisting | T-005 | Implemented | `REQUIRE_ROUTING_APPROVAL=true` (default) |
| M-009 | S3 bucket versioning enabled — malicious uploads can be reverted | T-005 | Implemented | `VersioningConfiguration: Enabled` |
| M-010 | Slack payload signature verification using HMAC-SHA256 with signing secret | T-006 | Implemented | `slack_verifier.py` validates `X-Slack-Signature` header |
| M-011 | SNS topic encrypted at rest with customer-managed AWS KMS key | T-007 | Implemented | `KmsMasterKeyId` in SAM template |
| M-012 | Least-privilege IAM policies scoped to specific resource ARNs | T-008 | Implemented | SES scoped to identity, SNS to topic, DynamoDB to table |
| M-013 | API Gateway default throttling (10,000 req/s burst, 5,000 sustained) | T-009 | Accepted | Default API Gateway limits sufficient for demo |
| M-014 | No secrets stored in system prompt; sensitive tool outputs not echoed to final response | T-010 | Implemented | Secrets retrieved at runtime from Secrets Manager |
| M-015 | EKS MCP Server is AWS-managed (`awslabs.eks-mcp-server@0.1.3`); version pinned | T-004 | Implemented | Pinned to specific version to prevent supply chain attacks |
| M-016 | Destructive agent tools (upgrade_eks_cluster) only execute in remediation mode behind approval token | T-004 | Implemented | `remediation_action` key required in payload |
| M-017 | IAM role grants no `eks:UpdateClusterVersion` by default — mutating tool exists in code but is IAM-denied at runtime | T-004, T-008 | Implemented | Defense-in-depth: even if approval flow is bypassed, the API call fails with AccessDenied. Customer must explicitly opt in to enable mutations. |
| M-018 | Event-level idempotency — DynamoDB conditional put prevents the same event ARN from being processed twice | T-011 | Implemented | Uses `APPROVAL_TABLE_NAME` with `idempotency#` prefix key; 24-hour TTL |
| M-019 | Lambda reserved concurrency capped at 10 — limits parallel notification bursts | T-011 | Implemented | `ReservedConcurrentExecutions: 10` on all Lambda functions |
| M-020 | Jira duplicate detection — searches for existing open tickets with matching event ARN label before creating new ones | T-011 | Implemented | `find_duplicate()` in JiraClient prevents duplicate tickets per event |

### Notification Flooding (T-011)

**Risk:** During a major AWS incident, many health events may arrive in a short burst. Each event triggers the Lambda independently, which may flood downstream channels (Slack, Jira, SNS → email).

**Existing controls that limit flooding:**

| Control | What it prevents | Limitation |
|---|---|---|
| Event-level idempotency (M-018) | Same event ARN processed twice | Does NOT prevent different event ARNs about the same underlying incident |
| Lambda concurrency cap (M-019) | More than 10 parallel invocations | Events queue in EventBridge; flooding is delayed, not prevented |
| Jira duplicate check (M-020) | Duplicate tickets for same event | Does NOT prevent N tickets from N different events during a burst |
| Agent status filter | Processing closed/resolved events | Only open/upcoming events processed — AWS sometimes sends both |

**Residual risks (accepted for PoC):**

| Channel | Flooding Scenario | Impact | Accepted? |
|---|---|---|---|
| **Slack** | 20 health events in 5 minutes → 20 Slack messages | Channel noise; Slack may rate-limit (1 req/sec for webhooks) — messages dropped silently | Yes |
| **Jira** | 20 events, each classified as BREAKING_CHANGE → up to 20 tickets | Team overwhelmed with tickets; Jira API may return 429 (rate limit) | Yes |
| **SNS → Email** | 20 events → 20 SNS publishes → 20 emails per subscriber | Inbox flood for ops team during incidents (when they're already busy) | Yes |
| **SES (approval emails)** | Multiple confirmed-impact events → multiple approval emails | Operator confusion about which to approve; SES sandbox limits (1 email/sec) may throttle | Yes |

**Recommended mitigations for production use:**

1. **SQS buffering with batch window** — Place an SQS queue between EventBridge and the Lambda with a `BatchWindow` of 60-300 seconds. The Lambda receives a batch of events and produces a consolidated notification instead of per-event messages.
2. **Slack rate limiting** — Add a per-channel cooldown (e.g., max 5 messages per 5 minutes). If exceeded, buffer messages and send a consolidated summary at the end of the window.
3. **SNS message deduplication** — Enable SNS FIFO topic with `MessageDeduplicationId` based on service + event type code to collapse similar events.
4. **Jira parent/child tickets** — During a burst, create one parent ticket for the incident and link individual events as sub-tasks rather than independent tickets.
5. **EventBridge input transformer** — Pre-filter events at the EventBridge rule level to only match specific services or severity levels, reducing the volume before Lambda invocation.

## Data Classification and Handling

### Classification Levels

| Level | Data Types | Handling Requirements |
|---|---|---|
| **RESTRICTED** | Jira API tokens, approval tokens (256-bit), Slack signing secrets | Stored in Secrets Manager/DynamoDB with AWS KMS encryption. Never logged. |
| **CONFIDENTIAL** | AWS account IDs, health event details, remediation payloads | Encrypted at rest (AWS KMS). Logged without sensitive fields. 7-day retention for tokens. |
| **INTERNAL** | Cost projections, team routing mappings, classification results | Encrypted in transit (TLS). Standard CloudWatch log retention. |
| **PUBLIC** | Architecture diagrams, deployment scripts (without secrets) | No special handling required. |

### Data Retention

- Approval tokens: 7 days (DynamoDB TTL)
- CloudWatch Logs: 90 days (configurable)
- Health event data: Transient (processed in-memory, published to SNS)
- Routing config: Persisted in Secrets Manager (versioned)

### Access Logging

| Data Store | Logging Mechanism | Retention | What is Logged |
|---|---|---|---|
| DynamoDB (ApprovalStore) | CloudTrail data events | 90 days | PutItem, GetItem, UpdateItem with principal and timestamp |
| S3 (RoutingConfigBucket) | S3 server access logging → AccessLogsBucket | 90 days | Object uploads, downloads with requester and timestamp |
| Secrets Manager | CloudTrail management events | 90 days | GetSecretValue, PutSecretValue with principal |
| SNS | CloudTrail management events | 90 days | Publish calls with principal |
| API Gateway | CloudWatch access logs | 90 days | Request ID, source IP, method, path, status |

## Key Management Strategy

### AWS KMS Keys

| Resource | Key Type | Rotation |
|---|---|---|
| DynamoDB (ApprovalStore) | Customer-managed CMK | Annual (automatic) |
| SNS Topic | Customer-managed CMK | Annual (automatic) |
| SQS Dead Letter Queue | Customer-managed CMK | Annual (automatic) |
| S3 (RoutingConfigBucket) | SSE-S3 (AES-256) | Managed by AWS |
| Secrets Manager | AWS-managed key | Managed by AWS |

### Key Access Controls

- Keys restricted to specific service principals via `kms:ViaService` conditions
- Lambda execution roles granted encrypt/decrypt only for their specific resources
- Key administration separated from key usage via IAM policies

### Infrastructure-as-Code Implementation

The encryption settings documented above are implemented in `aha_eventbridge_lambda/template.yaml`:

```yaml
# DynamoDB encryption (ApprovalStore)
SSESpecification:
  SSEEnabled: true
  SSEType: KMS
  KMSMasterKeyId: !Ref EncryptionKey

# SNS topic encryption
HealthEventSnsTopic:
  KmsMasterKeyId: !Ref EncryptionKey

# SQS DLQ encryption
DeadLetterQueue:
  KmsMasterKeyId: !Ref EncryptionKey

# S3 bucket encryption
BucketEncryption:
  ServerSideEncryptionConfiguration:
    - ServerSideEncryptionByDefault:
        SSEAlgorithm: AES256

# AWS KMS key with annual rotation
EncryptionKey:
  EnableKeyRotation: true
```

See the full template at `aha_eventbridge_lambda/template.yaml` for complete resource definitions.


## 3rd Party Service Approvals

### Anthropic Claude Sonnet (via Amazon Bedrock)

- **Service:** Anthropic Claude Sonnet 4 (`eu.anthropic.claude-sonnet-4-20250514-v1:0`)
- **Access method:** Amazon Bedrock managed service (pre-approved marketplace model)
- **Legal status:** Pre-approved via Amazon Bedrock marketplace — no separate legal review required
- **Data handling:** Amazon Bedrock guarantee — customer data not used for model training
- **Approved use cases:** Health event classification, routing document parsing
- **Review date:** May 2026

### AWS EKS MCP Server (awslabs.eks-mcp-server)

- **Service:** `awslabs.eks-mcp-server@0.1.3` via uvx
- **License:** Apache 2.0 (AWS Labs open source)
- **Access method:** Direct stdio connection (no Gateway required)
- **Legal status:** AWS Labs open source project — covered by Apache 2.0 license
- **Security posture:** AWS-managed, SigV4 authentication for API calls
- **Data handling:** Reads EKS cluster metadata only (no customer application data)
- **Approved use cases:** EKS cluster insights, upgrade readiness analysis
- **Flags:** `--allow-write` (for upgrade operations), `--allow-sensitive-data-access` (for cluster configs)
- **Review date:** May 2026

## Shared Responsibility Model

This solution follows the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/):

**AWS Responsibilities (Security OF the Cloud):**
- Physical infrastructure security for Lambda, DynamoDB, SNS, SQS, S3, API Gateway
- Managed service patching and maintenance (Amazon Bedrock, AgentCore Runtime)
- Underlying encryption infrastructure for AWS KMS and service-managed keys
- Network infrastructure and DDoS protection

**Customer Responsibilities (Security IN the Cloud):**
- IAM role and policy configuration (least-privilege access)
- AWS KMS key policies, rotation schedules, and access controls
- Application-level security controls (token validation, signature verification, input sanitization)
- Data classification and handling procedures
- Security monitoring, alerting, and incident response
- Secrets management (Jira API tokens, Slack signing secrets) including rotation
- S3 bucket policies, encryption settings, and access controls
- API Gateway throttling and request validation configuration

## Security Scan Results

### Latest Scan: May 08, 2026

- **Tool:** Slingshot (RUBRIC + Bandit + Checkov + CFN Guard)
- **Scan Coverage:** Full repository (all Python, YAML, Dockerfile, Markdown)
- **Critical Findings:** 0
- **High Findings:** 69 (all addressed or documented with compensating controls)

### Mitigation Summary

| Category | Count | Status |
|---|---|---|
| Infrastructure security (S3, SNS, SQS, DynamoDB encryption) | 12 | Implemented in template.yaml |
| AWS service naming standards | 8 | Fixed across all source files |
| Legal compliance (copyright headers, LICENSE) | 10 | Applied to all .py files |
| Hardcoded secrets/PII removal | 6 | Replaced with env vars |
| IAM policy scoping | 8 | SES scoped; Organizations/EKS wildcards documented as required |
| Code security (error handling, URL validation) | 7 | Implemented |
| AI/ML security documentation | 6 | Documented in this file |
| Architecture/design documentation | 5 | SECURITY.md + README.md |
| Build artifacts in repo | 3 | Removed, added to .gitignore |
| Shared responsibility documentation | 4 | Added to this file |

### Accepted Risks

| Finding | Justification | Compensating Controls |
|---|---|---|
| Organizations API `Resource: "*"` | AWS Organizations does not support resource-level permissions for these actions | IAM role trust policy restricts principals; CloudTrail logs all API calls; SCPs limit account scope |
| EKS MCP `Resource: "*"` | Preview service does not support resource-level permissions. Not granted in the default deployed template — only applies if customer adds `eks-mcp:CallPrivilegedTool` | IAM role trust policy restricts principals; default deployment has no EKS MCP permissions; will scope when GA supports it |
| AgentRuntime ARN wildcard suffix `*` | Required by the `bedrock-agentcore:InvokeAgentRuntime` API for session sub-resources | Scoped to specific runtime ARN prefix; only session sub-resources matched |
| AWS KMS key policy `Resource: "*"` | Standard pattern — KMS key policies are implicitly scoped to the key itself | Condition constraints added (SNS topicArn, SQS queueArn, CloudWatch Logs ARN) |
| In-memory data stores (cost_estimator, ticket_creator) | Demo/PoC only; production would use encrypted DynamoDB | Not deployed to production; data is transient |
| ECR repository not AWS KMS-encrypted | Uses default AES-256 encryption | Image scanning enabled on push; immutable tags enforced |

### Verification

- **Verified by:** Automated Slingshot scan + manual code review
- **Verification date:** May 08, 2026
- **Attestation:** All Critical/High findings have been addressed with code fixes or documented compensating controls


## Security Implementation Guide

### M-001: Token Generation (384-bit entropy)

```python
import secrets
token = secrets.token_urlsafe(48)  # 48 bytes = 384 bits, URL-safe base64 (64 chars)
```

### M-003: Single-Use Token Validation

```python
from boto3.dynamodb.conditions import Attr
table.update_item(
    Key={"token": token},
    UpdateExpression="SET #s = :approved, approved_at = :now",
    ConditionExpression=Attr("status").eq("pending") & Attr("expires_at").gt(int(time.time())),
    ExpressionAttributeNames={"#s": "status"},
    ExpressionAttributeValues={":approved": "approved", ":now": datetime.now().isoformat()},
)
# ConditionalCheckFailedException = token already used or expired
```

### M-005: Two-Step Approval (GET → confirm page, POST → execute)

```python
# GET /approve?token=xxx → returns HTML confirmation page
# POST /approve (form body: token=xxx) → executes remediation
# This prevents email security scanners from triggering actions via link pre-fetch
```

### M-009: S3 Bucket Versioning

```bash
aws s3api get-bucket-versioning --bucket phd-routing-config-${ACCOUNT_ID}
# Expected: {"Status": "Enabled"}
```

### M-010: Slack Signature Verification

```python
import hmac, hashlib
def verify_slack_signature(signing_secret, timestamp, body, signature):
    sig_basestring = f"v0:{timestamp}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)
```

### M-011: SNS Topic Encryption Verification

```bash
aws sns get-topic-attributes --topic-arn arn:aws:sns:eu-west-1:${ACCOUNT_ID}:aha-health-event-notifications \
  --query "Attributes.KmsMasterKeyId"
# Expected: ARN of the customer-managed AWS KMS key
```

### M-012: IAM Least-Privilege Verification

```bash
aws iam simulate-principal-policy \
  --policy-source-arn <lambda-role-arn> \
  --action-names ses:SendEmail \
  --resource-arns "arn:aws:ses:eu-west-1:${ACCOUNT_ID}:identity/unauthorized@example.com"
# Expected: implicitDeny (only configured identity allowed)
```

## Bias and Fairness Considerations

### Classification Bias Analysis

- **Potential bias:** LLM may have recency bias toward newer AWS services mentioned in training data
- **Mitigation:** Classification rules are explicit and deterministic in the system prompt (BREAKING_CHANGE > COST_IMPLICATION > SECURITY_RELATED). The LLM applies documented rules, not learned patterns.
- **Testing:** Validated classification on historical health events spanning 20+ AWS services

### Routing Fairness

- **Potential bias:** Resource-level routing may concentrate tickets on teams managing large clusters
- **Mitigation:** Routing config is human-authored (uploaded documents) and human-approved (Slack workflow). The LLM parses but does not decide routing.
- **Monitoring:** Monitor ticket distribution by team monthly; adjust routing config if imbalance detected

### Human Oversight

- All BREAKING_CHANGE classifications with confirmed impact require human approval before remediation
- SNS notifications include full reasoning for all classifications, enabling operator override
- Classification results are published (not silently acted upon) — humans review before taking action
