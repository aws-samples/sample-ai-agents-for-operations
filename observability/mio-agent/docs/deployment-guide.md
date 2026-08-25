# MIO Agent — Deployment Guide

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12+ | `python --version` |
| AWS CDK | v2.x | `npm install -g aws-cdk` |
| AWS CLI | v2.x | Configured with deployment permissions |
| Amazon Bedrock | — | Claude 3.5 Sonnet enabled in target region |

## Pre-Deployment: Enable Bedrock Model Access

1. Open [Amazon Bedrock console](https://console.aws.amazon.com/bedrock)
2. Navigate to **Model access** → **Manage model access**
3. Enable: **Anthropic Claude 3.5 Sonnet**

## Deployment Steps

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### 2. Run tests

```bash
pytest tests/ -v --cov=src/mio_agent
# Minimum 80% coverage required
```

### 3. Bootstrap CDK (first time only)

```bash
cd infrastructure
pip install -r requirements.txt
cdk bootstrap aws://ACCOUNT_ID/REGION
```

### 4. Review the deployment

```bash
cdk diff --context account=ACCOUNT_ID --context region=us-east-1
```

### 5. Deploy

```bash
cdk deploy --context account=ACCOUNT_ID --context region=us-east-1
```

This deploys:
- DynamoDB tables (assessments + accounts)
- S3 bucket (reports)
- SQS queue + DLQ
- Lambda functions (coordinator + 5 triggers)
- API Gateway REST API
- EventBridge rules (health events, deployments, weekly schedule)
- SSM parameters
- IAM roles

### 6. Verify deployment

```bash
# List outputs
aws cloudformation describe-stacks \
  --stack-name MIOAgentStack \
  --query 'Stacks[0].Outputs'
```

### 7. Run a test assessment

```bash
# Get API endpoint from stack outputs
API_URL=$(aws cloudformation describe-stacks \
  --stack-name MIOAgentStack \
  --query 'Stacks[0].Outputs[?OutputKey==`APIEndpoint`].OutputValue' \
  --output text)

# Trigger assessment (Tier 1 — no customer account needed for testing)
curl -X POST "${API_URL}assess" \
  --aws-sigv4 "aws:amz:us-east-1:execute-api" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "000000000000",
    "account_name": "Test Account",
    "access_tier": "tier1",
    "requested_by": "deployment-test"
  }'
```

## Updating

```bash
git pull
pip install -r requirements.txt
cdk deploy --context account=ACCOUNT_ID --context region=us-east-1
```

## Uninstalling

```bash
cdk destroy --context account=ACCOUNT_ID --context region=us-east-1
```

Note: DynamoDB tables and S3 bucket are set to `RETAIN` removal policy. Delete manually if needed:
```bash
aws dynamodb delete-table --table-name mio-agent-assessments
aws dynamodb delete-table --table-name mio-agent-accounts
aws s3 rb s3://BUCKET_NAME --force
```
