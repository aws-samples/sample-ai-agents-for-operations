# DART Agent — Technical Design

**Agent:** DART (Dataset Audit & Readiness for Training)  
**Pattern:** Strands on Fargate (Pattern #5)  

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        User / CI-CD                          │
│         "Check s3://bucket/train.jsonl for llama3-70b"       │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP / chat
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   ECS Fargate Task                           │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              DART Agent (agent.py)                  │    │
│  │         Strands SDK + Claude Sonnet 4               │    │
│  │                                                     │    │
│  │  Orchestrates tools in sequence, reasons across     │    │
│  │  results, generates final recommendation            │    │
│  └──────────────────────┬──────────────────────────────┘    │
│                         │                                    │
│    ┌────────────────────┼────────────────────┐              │
│    ▼                    ▼                    ▼              │
│  profile_dataset   detect_duplicates      scan_pii          │
│  (local Polars)    (datasketch MinHash)  (Comprehend)       │
│                                                              │
│    ┌────────────────────┬────────────────────┐              │
│    ▼                    ▼                    ▼              │
│  check_training_   estimate_training_   apply_safe_fixes    │
│  viability         cost                 (local Polars)      │
│  (tiktoken)        (pricing API)                            │
│                         │                                    │
│                         ▼                                    │
│              generate_preflight_report                       │
│              (Claude Sonnet 4 synthesis)                     │
└──────────────────────┬───────────────────────────────────────┘
                       │
          ┌────────────┼──────────────┐
          ▼            ▼              ▼
        S3           DynamoDB    CloudWatch
    (datasets,     (audit trail,   (logs,
    reports)       run metadata)   metrics)
```

---

## Agent Flow

1. User submits dataset path + target model
2. Agent calls `profile_dataset` — builds structural understanding
3. Agent calls `detect_duplicates` — quantifies duplication
4. Agent calls `scan_pii` — identifies compliance risk
5. Agent calls `check_training_viability` — validates model compatibility
6. Agent calls `estimate_training_cost` — computes financial impact
7. Agent reasons across all results — determines severity and priority
8. If autonomous mode: calls `apply_safe_fixes` automatically
9. If step-by-step: presents findings, asks for approval per fix
10. Agent calls `generate_preflight_report` — produces final GO/NO-GO
11. Report stored to S3 + DynamoDB, returned to user

---

## Project Structure

```
src/dart-agent/
├── agent.py                    # Strands agent entrypoint + handler
├── config.py                   # Environment-based configuration
├── tools/
│   ├── __init__.py
│   ├── profile_dataset.py      # Format detection, schema, stats
│   ├── detect_duplicates.py    # MinHash + exact dedup
│   ├── scan_pii.py             # Amazon Comprehend PII
│   ├── check_training_viability.py  # Token budget, leakage, format
│   ├── estimate_training_cost.py    # SageMaker/Bedrock pricing
│   ├── apply_safe_fixes.py     # Auto-fix safe issues
│   └── generate_preflight_report.py  # Final report synthesis
├── prompts/
│   └── system_prompt.md        # Agent system prompt
tests/
├── unit/
│   ├── test_profile_dataset.py
│   ├── test_detect_duplicates.py
│   ├── test_scan_pii.py
│   ├── test_check_training_viability.py
│   ├── test_estimate_training_cost.py
│   ├── test_apply_safe_fixes.py
│   └── test_generate_preflight_report.py
└── integration/
    └── test_agent_e2e.py
infra/
├── bin/
│   └── dart-agent.ts           # CDK app entrypoint
├── lib/
│   └── DartAgentStack.ts       # Main CDK stack
└── cdk.json
Dockerfile
requirements.txt
Makefile
README.md
MUTATING_ACTIONS.md
```

---

## Key Design Decisions

### Strands on Fargate (not Lambda)
Datasets can be multi-GB. Lambda's 15-minute timeout and 10GB ephemeral storage limit are real constraints. Fargate gives unlimited execution time and configurable memory (up to 30GB), which is necessary for in-memory MinHash processing of large corpora.

### Streaming Dataset Processing
All tools use Polars lazy evaluation or chunked iteration — datasets are never fully loaded into memory. This enables processing of files larger than available RAM.

### Amazon Comprehend for PII (not OSS)
Comprehend is AWS-native, requires no model download, stays within the AWS security boundary, and is already approved in the tech stack. Using `detect_pii_entities` batch API to minimise latency and cost.

### No Training Data to Bedrock
The agent sends only metadata summaries to Bedrock for reasoning — never raw training records. This is a critical design constraint for customer data privacy and trust.

### Immutable Input, New Output
The original dataset is never modified. All fixes produce a new file at `s3://bucket/dart-agent-output/<run-id>/cleaned_dataset.<ext>`. This preserves auditability and enables rollback.

### DynamoDB Audit Trail
Every run is logged with: `run_id`, `input_path`, `target_model`, `findings_summary`, `recommendation`, `records_before`, `records_after`, `timestamp`. PII detection results stored separately with TTL for EU AI Act compliance.

---

## IAM Permission Model

### Default Role (READ-ONLY for customer data)

```typescript
// Customer dataset bucket — READ ONLY
datasetBucket.grantRead(taskRole);

// Comprehend — batch PII detection
taskRole.addToPolicy(new PolicyStatement({
  actions: ['comprehend:DetectPiiEntities', 'comprehend:BatchDetectPiiEntities'],
  resources: ['*'],  // Comprehend has no resource-level permissions
}));

// Bedrock — Claude Sonnet 4 only
taskRole.addToPolicy(new PolicyStatement({
  actions: ['bedrock:InvokeModel'],
  resources: [`arn:aws:bedrock:${region}::foundation-model/anthropic.claude-sonnet-4-20250514-v1:0`],
}));

// Agent-internal writes — scoped to agent's own resources
agentOutputBucket.grantReadWrite(taskRole);  // output only, tagged agent-managed:true
auditTable.grantReadWriteData(taskRole);
```

### What Requires Customer Opt-In
- Writing redacted datasets to customer's source bucket (default: separate output bucket)
- See MUTATING_ACTIONS.md

---

## Data Flow

```
Input:  s3://customer-bucket/training-data.jsonl  (READ)
Output: s3://dart-agent-output/<run-id>/
        ├── cleaned_dataset.jsonl    (fixed dataset)
        ├── preflight_report.json    (structured report)
        └── preflight_report.md      (human-readable report)

Audit:  DynamoDB dart-agent-audit-trail
        └── run_id, timestamp, recommendation, findings_summary, record_counts
```

---

## Pricing Model (What Agent Tells Customer)

The `estimate_training_cost` tool calculates:

| Cost Component | Formula |
|---|---|
| SageMaker training | `(total_tokens / tokens_per_second) × instance_hourly_rate × num_nodes` |
| Bedrock fine-tuning | `total_tokens × bedrock_ft_price_per_token` |
| Wasted cost (dupes) | `training_cost × (duplicate_ratio)` |
| DART agent run cost | Fargate task: ~$0.05 per run for typical dataset |

---

## Deployment

Single command deployment:
```bash
make deploy ENV=staging  # or prod
```

CDK stack creates:
- ECS Fargate task definition (4 vCPU, 8GB RAM default)
- ECS Cluster
- IAM execution role + task role
- S3 bucket for output (dart-agent-output-{account}-{region})
- DynamoDB table for audit trail (dart-agent-audit-trail)
- CloudWatch log group (/dart-agent/{env})
- ECR repository for Docker image

---

## Observability

Every tool invocation emits a structured log:
```json
{
  "event": "tool_invocation",
  "run_id": "run-20260907-abc123",
  "tool_name": "scan_pii",
  "duration_ms": 4231,
  "success": true,
  "records_processed": 50000,
  "pii_found": 847
}
```

CloudWatch alarms on:
- Tool failure rate > 10%
- Run duration > 15 minutes
- Comprehend throttling errors
