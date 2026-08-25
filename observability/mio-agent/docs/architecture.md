# MIO Agent — Architecture Guide

## Overview

MIO Agent is a multi-agent AI system built on **Amazon Bedrock Agents** with **Claude 3.5 Sonnet** as the foundation model. It uses an event-driven, serverless architecture that scales to zero when idle and handles burst workloads via SQS queuing.

## System Components

### Coordinator Agent
The central orchestrator. Receives an `AssessmentRequest`, determines the investigation strategy based on access tier and discovered services, delegates to specialist sub-agents, and computes the final OMS score.

**Technology:** Amazon Bedrock Agent + AWS Lambda (Python 3.12)

### Specialist Sub-Agents

| Agent | Responsibility |
|---|---|
| CloudWatch Analyst | Analyzes alarms, metrics coverage, log groups, X-Ray tracing, dashboards |
| IaC Scanner | Parses CDK/CloudFormation/Terraform templates for monitoring gaps |
| Third-Party Validator | Detects Datadog/Dynatrace/NewRelic/Splunk coverage gaps |
| Narrative Agent | Converts findings to audience-appropriate language via Bedrock |

### Trigger Layer

Five trigger mechanisms funnel assessment requests into the SQS queue:

1. **Support Case Handler** — SNS/EventBridge event when P1/P2 case opened
2. **Deployment Monitor** — CloudTrail events for new resource creation  
3. **Health Event Handler** — AWS Health events affecting customer accounts
4. **Scheduler** — EventBridge Scheduler (weekly, every Monday 8AM UTC)
5. **API Handler** — API Gateway REST API for on-demand requests

### Storage Layer

| Store | Purpose |
|---|---|
| DynamoDB `mio-agent-assessments` | Assessment history, OMS scores, trend data |
| DynamoDB `mio-agent-accounts` | Customer account registry with access configuration |
| S3 `mio-agent-reports` | Generated report storage with presigned URL sharing |
| SSM Parameter Store | Runtime configuration (queue URLs, feature flags) |

## Data Flow

```
Trigger Event
    → Lambda Trigger Handler
    → SQS Queue (decouples and buffers requests)
    → Coordinator Lambda (dequeues, orchestrates)
    → Parallel specialist analysis
    → OMS score calculation
    → Narrative Agent (Bedrock) generates reports
    → DynamoDB + S3 persistence
    → Report URL returned to caller
```

## Security Architecture

### Cross-Account Access
MIO Agent uses STS `AssumeRole` to access customer accounts. The customer deploys a read-only IAM role `MIOAgentReadOnly` with no write permissions. An optional External ID can be configured for additional security.

### Data Isolation
- Customer account data is processed in memory and persisted only to the TAM's AWS account
- TTL of 365 days on all DynamoDB records
- S3 reports are encrypted with AES-256 and accessible only via presigned URLs
- No data crosses AWS regions

### IAM Least Privilege
- Coordinator Lambda has only the permissions it needs (DynamoDB, S3, SQS, Bedrock, STS AssumeRole to `MIOAgentReadOnly`)
- Customer read-only role grants only `Describe*`, `List*`, `Get*` actions — no write permissions

## Scalability

The SQS-based architecture allows MIO Agent to process multiple customer accounts in parallel. For large TAM portfolios (50+ accounts), the weekly scheduler fans out all requests to SQS simultaneously, and Lambda scales to process them concurrently within AWS Lambda concurrency limits.
