# MIO Agent — TAM User Guide

## What MIO Agent Does for You

MIO Agent gives you an evidence-based observability assessment for any customer account in minutes — replacing hours of manual preparation before QBRs, EBCs, and support case calls.

## When MIO Agent Runs Automatically

### Before a Support Case Call
When your customer opens a P1 or P2 support case, MIO Agent automatically runs an observability assessment and delivers a brief to you within 10 minutes — before you join the call. You'll know:
- What monitoring the customer has (and doesn't have)
- Whether better monitoring would have caught this incident earlier
- Specific talking points for the call

### Weekly — Every Monday Morning
You receive a prioritized list of all your accounts ranked by observability risk. Accounts that declined week-over-week are flagged first. This is your weekly preparation list for proactive customer outreach.

### When New Resources Are Deployed
If your customer deploys new Lambda functions, EC2 instances, or RDS databases without configuring monitoring, MIO Agent flags this automatically.

## On-Demand: Before a QBR or EBC

Use the API to trigger a full assessment before any important customer meeting:

```bash
curl -X POST https://YOUR_API_ENDPOINT/v1/assess \
  -H "Content-Type: application/json" \
  --aws-sigv4 "aws:amz:us-east-1:execute-api" \
  -d '{
    "account_id": "CUSTOMER_ACCOUNT_ID",
    "account_name": "Customer Name",
    "access_tier": "tier3",
    "role_arn": "arn:aws:iam::CUSTOMER_ACCOUNT_ID:role/MIOAgentReadOnly",
    "requested_by": "your-alias"
  }'
```

The report is ready in under 5 minutes.

## Understanding the OMS Score

| Score | Risk | What to Tell the Customer |
|---|---|---|
| 4.0–5.0 | 🟢 LOW | "Your observability posture is strong. Let's focus on maintaining it." |
| 3.0–3.9 | 🟡 MEDIUM | "You have solid foundations with a few specific gaps to address." |
| 2.0–2.9 | 🟠 HIGH | "There are significant gaps that are increasing your incident risk." |
| 1.0–1.9 | 🔴 CRITICAL | "This needs immediate attention — you are at high risk of extended outages." |

## Using Reports in Customer Conversations

The TAM brief includes ready-to-use talking points. The customer report is formatted for direct sharing with customer engineering and CTO-level stakeholders.

**For a QBR:**
- Use the TAM brief talking points to structure your opening
- Share the customer health report PDF/markdown as a leave-behind
- Reference the "before/after" narrative to show projected improvement

**For an EBC:**
- Use the leadership summary format to show portfolio-level trends
- Connect OMS scores to support case reduction business value

## Setting Up Tier 3 Access with a Customer

See the [Customer Onboarding Guide](customer-onboarding.md) for step-by-step instructions to deploy the read-only IAM role in the customer account.
