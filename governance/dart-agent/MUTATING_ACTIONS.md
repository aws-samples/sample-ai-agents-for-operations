# ⚠️ Optional: Enable Write Actions

DART Agent is **read-only by default** on customer data. The agent's IAM role only grants read permissions on customer S3 buckets. All output is written to the agent's own dedicated output bucket (`dart-agent-output-{account}-{region}`).

The agent includes tools that **can** write redacted datasets back to a customer-specified path. **These capabilities are disabled by default.** They will return `Access Denied` errors until you explicitly grant the required permissions.

---

## Why This Is Disabled by Default

DART Agent was built to inspect and certify training data — not to modify it. The default read-only mode means it cannot accidentally overwrite your source data, even if the AI reasoning produces an unexpected action plan.

All fixed and redacted datasets are written to the separate output bucket by default, preserving the original data immutably.

---

## To Enable Write-Back to Customer S3 Buckets (Optional)

If you want the agent to write redacted or fixed datasets directly back to your source bucket, grant the following additional permissions to the agent's ECS task role (`dart-agent-task-role-{environment}`):

### Required IAM Permissions

| Tool | Required Permission | Resource | What It Does |
|------|-------------------|----------|--------------|
| `scan_pii` (with `produce_redacted_copy=True`) | `s3:PutObject` | Your source bucket | Writes redacted copy to customer bucket |
| `apply_safe_fixes` | `s3:PutObject` | Your source bucket | Writes cleaned dataset to customer bucket |

### Sample IAM Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DartAgentWriteToSourceBucket",
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/dart-agent-output/*",
      "Condition": {
        "StringEquals": {
          "s3:prefix": "dart-agent-output/"
        }
      }
    }
  ]
}
```

**Add this policy ONLY if:**
- [ ] You have reviewed the tool source code in `src/dart-agent/tools/`
- [ ] You have tested the agent in a non-production environment first
- [ ] You accept responsibility for data written by the agent
- [ ] You have approval from your organisation's security team

### How to Apply

1. Navigate to IAM in the AWS Console
2. Find the role: `dart-agent-task-role-{environment}`
3. Attach the above inline policy
4. The agent will immediately have write access on the next invocation

---

## Data Safety Guarantees (Always Enforced)

Regardless of whether write-back is enabled:

- ✅ The **original input dataset is never deleted** — the agent only creates new files
- ✅ All output is written under a unique `run-id` prefix — no silent overwrites
- ✅ PII redaction **requires explicit approval** in step-by-step mode
- ✅ All writes are logged to the DynamoDB audit trail with timestamp and run ID
- ✅ The agent **never sends raw training data to Amazon Bedrock** — only metadata summaries
