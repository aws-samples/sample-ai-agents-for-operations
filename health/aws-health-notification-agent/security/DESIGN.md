# AWS Health Notification Classifier — Design Document

## System Overview

AI-powered system that classifies AWS Health notifications, performs impact analysis, and orchestrates remediation with human approval.

## Component Architecture

| Component | Responsibility | Runtime |
|---|---|---|
| aha-eventbridge-lambda | Routes EventBridge health events to AgentCore or SNS | Lambda (Python 3.13) |
| phd-notification-classifier | Classifies events, analyzes impact, publishes results | AgentCore Runtime (ARM64 container) |
| approval-lambda | Human approval flow (GET confirm, POST execute) | Lambda (Python 3.13) |
| routing-config-lambda | Parses routing docs via Amazon Bedrock, posts to Slack | Lambda (Python 3.13) |
| routing-approval-lambda | Handles Slack interactive payloads for routing approval | Lambda (Python 3.13) |

## Architecture Diagram

See `assets/PHD_Agent.png` and the Mermaid diagram in `README.md`.

## Security Considerations

### 1. AI/LLM Security Controls
- Structured system prompt with explicit delimiters separating instructions from data
- Output validated against JSON schema before downstream processing
- Human approval gate for all destructive actions (EKS upgrades)
- See `phd_notification_classifier/prompts.py` docstring for full controls

### 2. Multi-Account Access Controls
- AWS Organizations API: read-only (DescribeAccount, ListParents, ListTagsForResource)
- Scoped to accounts within the same organization
- No cross-account write operations

### 3. Cryptographic Token Security
- High-entropy tokens (384-bit via `secrets.token_urlsafe(48)`) for approval workflow
- Single-use enforcement via DynamoDB conditional writes
- 7-day TTL with automatic expiration
- Two-step flow prevents email pre-fetch attacks

### 4. Data Protection
- Encryption at rest: AWS KMS CMK for DynamoDB, SNS, SQS; SSE-S3 for routing bucket
- Encryption in transit: TLS 1.2+ on all API calls; S3 bucket policy denies non-TLS
- Secrets in Secrets Manager (never in code or env vars)

### 5. Least-Privilege IAM
- Each Lambda has its own execution role with resource-scoped permissions
- SES scoped to specific identity ARN
- SNS scoped to specific topic ARN
- DynamoDB scoped to specific table ARN

## Data Flow: Event Classification

```
EventBridge → Lambda (parse, filter status) → AgentCore (classify, analyze)
→ Lambda (validate JSON, format) → SNS (publish) + Jira (create ticket) + Slack (notify)
```

## Data Flow: Remediation Approval

```
Lambda (confirmed impact) → DynamoDB (store token) → SES (send approval email)
→ Human clicks link → API Gateway → Approval Lambda (validate token)
→ AgentCore (execute remediation) → SES (confirmation email)
```

## Error Handling

- Dead Letter Queue for all Lambda functions (failed invocations)
- Exponential backoff retry for AgentCore transient errors (1s, 2s, 4s)
- Graceful fallback: if AgentCore fails, raw response published to SNS
- Routing config: retry once with error-correction prompt on validation failure

## Monitoring

- Structured JSON logging with event ARN correlation IDs
- CloudWatch Logs with 90-day retention
- API Gateway access logs for approval endpoints
- CloudTrail for all AWS API calls
