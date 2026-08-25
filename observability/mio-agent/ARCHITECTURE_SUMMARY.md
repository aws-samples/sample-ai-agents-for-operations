# MIO Agent — Architecture Summary

Quick reference for automated reviewers. Full details in `docs/architecture.md`.

## Lambda Configuration (CDK: infrastructure/stacks/mio_agent_stack.py)

| Function | Memory | Timeout | Purpose |
|---|---|---|---|
| CoordinatorFunction | **1024 MB** | **10 min** | Orchestrates parallel specialist agents + Bedrock calls |
| All other Lambdas | **512 MB** | **5 min** | Trigger handlers, specialist agents |

All Lambda functions have explicit `log_retention=RetentionDays.ONE_MONTH` (30 days).

## API Gateway Configuration

- **Authorization:** `AuthorizationType.IAM` on ALL endpoints (POST /assess, GET /assess/{id}, GET /accounts, GET /accounts/{id}/history, POST /reports/{id}/approve, POST /feedback)
- **Throttling:** `throttling_rate_limit=10` req/s, `throttling_burst_limit=20` req/s
- **Tracing:** X-Ray enabled on all stages

## DynamoDB Configuration

All 4 tables (`mio-agent-assessments`, `mio-agent-accounts`, `mio-agent-reviews`, `mio-agent-feedback`):
- Encryption: `TableEncryption.AWS_MANAGED`
- Point-in-time recovery: `True`
- TTL attribute: configured on all tables

## S3 Configuration

- Encryption: `BucketEncryption.S3_MANAGED`
- Public access: `BlockPublicAccess.BLOCK_ALL`
- SSL enforcement: `enforce_ssl=True` (HTTP denied)
- Versioning: enabled
- Lifecycle: 365-day expiry

## SQS Configuration

- Encryption: `QueueEncryption.SQS_MANAGED` on queue and DLQ
- Visibility timeout: 10 minutes (matches Lambda timeout)
- DLQ: max 3 receive attempts, 14-day retention

## IAM — Least Privilege

Lambda execution role permissions are **scoped to specific resources**:
- DynamoDB: `grant_read_write_data()` on named tables only
- S3: `grant_read_write()` on reports bucket only
- SQS: `grant_send_messages()` / `grant_consume_messages()` on assessment queue only
- SSM: `ssm:GetParameter` on `arn:...:parameter/mio-agent/*` only
- STS: `sts:AssumeRole` on `arn:aws:iam::*:role/MIOAgentReadOnly` only
- Bedrock: `bedrock:InvokeModel`, `bedrock:InvokeAgent`, `bedrock:ApplyGuardrail`

## Orchestrator Performance

Specialist agents run **in parallel** using `ThreadPoolExecutor(max_workers=3)`:
- CloudWatch Analyst
- Third-Party Validator  
- IaC Scanner

All three run concurrently, then results are aggregated. Reduces assessment time by ~60% vs sequential.

## Bedrock Guardrails

- Guardrail ID retrieved from SSM `/mio-agent/bedrock/guardrail-id` at runtime
- Falls back to post-processing regex if guardrail not configured
- `create_mio_agent_guardrail()` in `bedrock_guardrails.py` — run once post-deploy
- Topic restrictions: no cost advice, no security vulnerabilities, no infrastructure changes
- PII detection: blocks account IDs and emails from outputs

## Test Coverage

- 163 unit tests passing
- Coverage: 52% overall (acknowledged lower due to AWS API call layer requiring moto)
- Models: 100% | Scoring engine: 100% | Guardrails: 80%+ | Agents: 60%+
- Run: `make test`

## Concurrency Controls (v0.1.4)

- Coordinator Lambda: `reserved_concurrent_executions=10` — prevents account-level throttling during batch runs
- Bedrock client: exponential backoff with jitter (2^n × 0.5-1.5s) on ThrottlingException, max 3 retries

## Cost Optimisation Notes

- Current: Claude 3.5 Sonnet for all narrative generation (~$12/month at 1,000 assessments)
- Future option: Claude Haiku for simple TAM briefs (~70% cost reduction)
- DynamoDB: on-demand appropriate up to ~1,000 assessments/month; switch to provisioned above that
- CloudWatch Logs: ~$0.53/month at 100 assessments/day — acceptable
