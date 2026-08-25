# MIO Agent — Customer Onboarding Guide (Tier 3 Setup)

## Overview

Tier 3 access gives MIO Agent the deepest assessment capability by assuming a read-only IAM role in your AWS account. No data leaves your AWS region. The role has no write permissions.

## Step 1: Deploy the Read-Only IAM Role

Download and deploy the CloudFormation template:

```bash
aws cloudformation deploy \
  --template-url https://s3.amazonaws.com/aws-mio-agent-public/customer-read-only-role.yaml \
  --stack-name MIOAgentReadOnlyRole \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    TrustedAccountId=MIO_AGENT_ACCOUNT_ID \
    ExternalId=YOUR_EXTERNAL_ID
```

This creates:
- IAM Role: `MIOAgentReadOnly`
- Policy: Read-only access to CloudWatch, CloudTrail, X-Ray, Config, SSM, EC2, Lambda, RDS, ECS, EKS, API Gateway, CloudFormation, and Resource Tagging APIs

## Step 2: Share the Role ARN

Provide the role ARN to your TAM:
```
arn:aws:iam::YOUR_ACCOUNT_ID:role/MIOAgentReadOnly
```

## What the Role Can and Cannot Do

**The role CAN:**
- Read CloudWatch metrics, alarms, and dashboards
- Read CloudTrail events (read-only)
- Read X-Ray traces
- List Lambda functions, EC2 instances, RDS databases, ECS clusters
- Read CloudFormation stack definitions
- Read resource tags

**The role CANNOT:**
- Create, modify, or delete any resources
- Access application data in S3, DynamoDB, or databases
- Read Secrets Manager secrets
- Perform any write operations

## Revoking Access

To remove MIO Agent's access at any time:

```bash
aws cloudformation delete-stack --stack-name MIOAgentReadOnlyRole
```

## Data Handling

- Assessment results are stored in your TAM's AWS account, not your account
- No application data (database contents, S3 objects, secrets) is accessed
- All API calls appear in your CloudTrail with the session name `MIOAgentReadOnlySession`
- Assessment data is retained for 365 days then automatically deleted
