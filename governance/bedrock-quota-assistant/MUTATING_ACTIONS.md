# Mutating Actions

This agent includes a tool that creates AWS Support cases. The agent's IAM role includes
the required permissions, and the tool will execute when the user explicitly confirms
submission.

## Safeguards

Multiple safeguards prevent accidental case creation:

1. **Agent-level confirmation gate** — The `submit_quota_increase_case` tool requires
   `confirm: "yes"` as an explicit parameter. The system prompt instructs the agent to
   never call this tool without clear user approval (e.g., "yes, submit it", "go ahead").
2. **Draft-first workflow** — A draft must be generated via `draft_quota_increase_request`
   before submission is possible. The agent presents the draft for review first.
3. **Support plan requirement** — The AWS Support API requires a Business or Enterprise
   Support plan. If the account lacks one, the API returns `SubscriptionRequiredException`
   and no case is created.

## Mutating Tools

| Tool | Granted Permissions | What It Does |
|------|-------------------|--------------|
| `submit_quota_increase_case` | `support:CreateCase`, `support:DescribeCases`, `support:DescribeSeverityLevels`, `support:DescribeServices` | Creates an AWS Support case requesting an Amazon Bedrock quota increase |

### Tool Behavior

- The tool requires explicit user confirmation (`confirm: "yes"`) before submission.
- A draft must be generated first via `draft_quota_increase_request`.
- The Support API is called in `us-east-1` regardless of the agent's deployed region.
- Requires a Business or Enterprise Support plan on the target account.

> Note: The AWS Support API does not support resource-level permissions — `Resource: "*"` is
> required in the IAM policy. This is an AWS limitation, not a policy choice.

## Risk Assessment

- **Blast radius:** Creating a Support case is non-destructive. It does not modify
  infrastructure, data, or access controls.
- **Cost implication:** A successful quota increase raises the TPM/RPM ceiling, which
  allows more model invocations to proceed without throttling. This can lead to higher
  Amazon Bedrock usage costs. Ensure the requested limits align with your budget and
  that appropriate cost monitoring (e.g., AWS Budgets, CloudWatch billing alarms) is
  in place before requesting an increase.
- **Reversibility:** Cases can be closed via the AWS Support Console at any time.
  However, once a quota increase is approved and applied, reverting to the lower limit
  requires a separate support request.
- **Audit trail:** Every submitted case is logged with its case ID and tracked in the
  AWS Support Console.
