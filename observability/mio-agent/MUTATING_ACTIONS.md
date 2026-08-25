# Mutating Actions

MIO Agent is **read-only by default**. It never modifies customer AWS environments.

## What MIO Agent Can Do Without Additional Permissions

- Read CloudWatch metrics, alarms, dashboards
- Read CloudTrail events (read-only)
- Read X-Ray traces
- List Lambda functions, EC2 instances, RDS databases, ECS clusters
- Read CloudFormation stacks
- Read resource tags
- Read SSM inventory

## What MIO Agent Cannot Do (By Design)

MIO Agent has **no write permissions** to customer accounts. The following actions are explicitly excluded from the `MIOAgentReadOnly` IAM role:

- Create, modify, or delete any AWS resource
- Write to S3, DynamoDB, or any data store in the customer account
- Modify IAM policies or roles
- Execute Lambda functions
- Change security group rules or network ACLs

## If You Want MIO Agent to Remediate Findings (Optional Future Extension)

MIO Agent does not implement auto-remediation. If you want to extend MIO Agent to automatically fix findings (e.g., enable X-Ray tracing on Lambda functions), you must:

1. Create a separate `MIOAgentRemediator` IAM role with only the specific write permissions needed
2. Add that role ARN to the CDK context
3. Implement remediation tools following the same evidence-anchoring guardrail pattern
4. Require explicit customer opt-in per remediation action

**No mutating actions are included in the default deployment.**
