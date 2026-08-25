# MIO Agent — Monitoring Intelligence and Observability Agent

> **⚠️ This is sample code for non-production use only.**
> It is provided as a reference implementation to accelerate your own development.
> You should work with your security and legal teams to meet your organizational
> security, regulatory, and compliance requirements before deploying any part of
> this code in a production environment.

An agentic AI system built on **Amazon Bedrock** that continuously assesses the monitoring and observability posture of your AWS environment — and tells you exactly what to fix before an incident exposes the gap.

---

## The Problem

Enterprise teams running workloads on AWS often don't know their monitoring is insufficient until a production incident reveals a blind spot. By then, it's too late — slow detection adds minutes or hours to resolution time, and the root cause analysis starts from scratch.

MIO Agent gives you a clear, evidence-based picture of your observability coverage before incidents happen.

---

## What MIO Agent Does

MIO Agent autonomously:

1. **Discovers** all running services in your AWS account
2. **Analyzes** CloudWatch configuration, IaC templates, and third-party monitoring tool coverage
3. **Scores** your observability maturity across 5 dimensions (1.0–5.0 scale)
4. **Generates** prioritized gap reports with specific, implementation-ready recommendations
5. **Monitors continuously** — triggered by new deployments, AWS Health events, or a weekly schedule

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        MIO Agent System                              │
│                                                                      │
│  ┌──────────────┐    ┌─────────────────────────────────────────┐   │
│  │  Triggers    │    │           Coordinator Agent              │   │
│  │              │───▶│  (Amazon Bedrock — configurable model)   │   │
│  │ • Deployment │    └──────────┬──────────────────────────────┘   │
│  │ • AWS Health │               │                                    │
│  │ • Schedule   │    ┌──────────▼──────────────────────────────┐   │
│  │ • On-Demand  │    │         Specialist Sub-Agents            │   │
│  └──────────────┘    │                                          │   │
│                       │ CloudWatch  │  IaC Scanner  │ 3rd Party │   │
│                       │ Analyst     │               │ Validator │   │
│                       │             │               │           │   │
│                       │         Narrative Agent                 │   │
│                       └─────────────────────────────────────────┘   │
│                                                                      │
│         DynamoDB (history) │ S3 (reports) │ SQS (requests)         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Observability Maturity Score (OMS)

The OMS is a weighted score from 1.0 (poor) to 5.0 (excellent) across 5 dimensions:

| Dimension | Weight | What It Measures |
|---|---|---|
| Metrics Coverage | 25% | Are the right metrics being collected for every running service? |
| Alerting Quality | 25% | Are alarms correctly configured with actionable thresholds? |
| Log Intelligence | 20% | Are logs structured, retained, and queryable? |
| Distributed Tracing | 20% | Is end-to-end request tracing enabled? |
| Incident Readiness | 10% | Are runbooks, dashboards, and on-call paths configured? |

**Risk levels:** LOW (4.0+) | MEDIUM (3.0–3.9) | HIGH (2.0–2.9) | CRITICAL (below 2.0)

---

## Access Tiers

MIO Agent supports three data access tiers, all producing the same output format:

| Tier | What It Uses | What You Need to Do |
|---|---|---|
| **Tier 1** | AWS Health events, Trusted Advisor findings, service adoption signals | Nothing — works immediately |
| **Tier 2** | IaC templates (CDK, CloudFormation, Terraform), CloudWatch exports | Upload your artifacts |
| **Tier 3** | Live read-only access to your AWS account | Deploy a read-only IAM role |

All tiers produce the same report format with a confidence level indicator reflecting the depth of analysis.

---

## Event-Driven Triggers

| Trigger | When | What Happens |
|---|---|---|
| Deployment | New Lambda, EC2, RDS, or ECS resource created | Agent checks if monitoring was provisioned |
| AWS Health | Health event affects your account | Agent assesses whether your monitoring would have detected the impact |
| Scheduled | Every Monday 8 AM UTC | Weekly observability assessment |
| On-Demand | API call | Full assessment in under 5 minutes |

---

## Prerequisites

- AWS account with Amazon Bedrock access
- AWS CDK v2: `npm install -g aws-cdk`
- Python 3.12+
- AWS CLI configured

**Default model:** Claude Sonnet 4 (`anthropic.claude-sonnet-4-20250514-v1:0`) — configurable via the `BEDROCK_MODEL_ID` environment variable. Any Bedrock-hosted model that supports the Converse API can be used.

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/aws-samples/sample-ai-agents-for-operations.git
cd sample-ai-agents-for-operations/mio-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Deploy

```bash
cd infrastructure
pip install -r requirements.txt
cdk bootstrap aws://YOUR_ACCOUNT_ID/YOUR_REGION
cdk deploy --context account=YOUR_ACCOUNT_ID --context region=YOUR_REGION
```

### 3. Register your account (Tier 1 — no additional setup needed)

```bash
aws dynamodb put-item \
  --table-name mio-agent-accounts \
  --item '{
    "account_id": {"S": "YOUR_ACCOUNT_ID"},
    "account_name": {"S": "My Production Account"},
    "access_tier": {"S": "tier1"},
    "enabled": {"BOOL": true}
  }'
```

### 4. Trigger an assessment

```bash
API_URL=$(aws cloudformation describe-stacks \
  --stack-name MIOAgentStack \
  --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
  --output text)

curl -X POST "${API_URL}assess" \
  --aws-sigv4 "aws:amz:YOUR_REGION:execute-api" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "YOUR_ACCOUNT_ID",
    "account_name": "My Production Account",
    "access_tier": "tier1"
  }'
```

### 5. For Tier 3 (full depth) — deploy read-only role

```bash
aws cloudformation deploy \
  --template-file docs/customer-read-only-role.yaml \
  --stack-name MIOAgentReadOnlyRole \
  --capabilities CAPABILITY_NAMED_IAM
```

Then update your account record with `"access_tier": "tier3"` and the role ARN.

---

## Sample Output

```
ACCOUNT: My Production Account
OMS: 2.8 / 5.0   ▲ +0.3 from last assessment   RISK: HIGH

TOP GAPS:

1. 47 EC2 instances running — only 31 have detailed monitoring enabled
   Fix: Enable detailed monitoring on 16 instances via SSM Run Command

2. RDS cluster has no Performance Insights retention beyond 7 days
   Fix: Extend to 93 days — 1-click in console, ~$2/month additional

3. Lambda function order-processor-prod has no X-Ray tracing
   Fix: Add AWS_XRAY_TRACING_NAME env var + AWSXRayDaemonWriteAccess policy

RECENT DEPLOYMENTS WITHOUT MONITORING: 2
```

---

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v --cov=src/mio_agent --cov-report=term-missing
```

---

## Project Structure

```
mio-agent/
├── src/mio_agent/          # Agent source code
│   ├── agents/             # Specialist sub-agents
│   ├── coordinator/        # Orchestrator and scoring engine
│   ├── models/             # Pydantic data models
│   ├── tools/              # AWS API tool functions
│   ├── triggers/           # Lambda trigger handlers
│   └── guardrails/         # Input validation and confidence gating
├── tests/                  # Unit and integration tests
├── infrastructure/         # AWS CDK stacks
├── examples/               # Sample output documents
└── docs/                   # Architecture and deployment guides
```

---

## Security

MIO Agent follows least-privilege security principles:

- **Read-only access** to your AWS account — never requests write permissions
- **All API endpoints** use IAM authorization
- **No data stored** beyond assessment TTL (365 days, configurable)
- **Reports encrypted** at rest in S3 with AES-256

See [RESPONSIBLE_AI.md](RESPONSIBLE_AI.md) for the full guardrail architecture.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

This project is licensed under the MIT-0 License. See [LICENSE](LICENSE).
