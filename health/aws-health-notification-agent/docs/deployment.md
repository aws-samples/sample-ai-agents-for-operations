# Deployment Guide

## Deploying with AWS Health Aware (AHA)

If your organization already runs [AWS Health Aware (AHA)](https://github.com/aws-samples/aws-health-aware) to aggregate health events from multiple accounts, this classifier integrates directly with AHA's EventBridge output.

### How AHA integration works

AHA collects AWS Health events from all member accounts in your organization and forwards them to a custom EventBridge bus (typically named `aha-eb01`). This classifier subscribes to that bus and processes AHA-forwarded events, which include enriched metadata like account names and entity URLs.

```
Member Account 1 ─┐
Member Account 2 ─┤─── AHA ───▶ Custom EventBridge Bus (aha-eb01) ───▶ This Classifier
Member Account N ─┘              (management account)
```

### Step 1: Find your AHA EventBridge bus name

AHA creates a custom EventBridge bus during its deployment. Find it with:

```bash
# List custom EventBridge buses in your management account
aws events list-event-buses --region eu-west-1 \
  --query "EventBuses[?Name!='default'].Name" --output table
```

The bus is typically named `aha-eb01` (AHA's default). If your AHA deployment uses a different name, use that value in Step 3.

### Step 2: Verify AHA is forwarding events

Confirm AHA is actively forwarding events to the bus:

```bash
# Check recent events on the AHA bus (requires CloudTrail data events enabled)
aws events describe-event-bus --name aha-eb01 --region eu-west-1

# Verify AHA's EventBridge rule exists and is enabled
aws events list-rules --event-bus-name aha-eb01 --region eu-west-1 \
  --query "Rules[].{Name:Name,State:State}" --output table
```

You can also trigger a test event from AHA's management account to verify the pipeline is active.

### Step 3: Deploy with AHA bus

```bash
./deploy.sh --aha-event-bus "aha-eb01"
```

Or with all integrations:

```bash
./deploy.sh \
  --region eu-west-1 \
  --aha-event-bus "aha-eb01" \
  --slack-webhook "https://hooks.slack.com/triggers/..." \
  --jira-url "https://myorg.atlassian.net" \
  --jira-project "OPS" \
  --jira-email "ops@company.com" \
  --jira-secret-arn "arn:aws:secretsmanager:eu-west-1:123456789012:secret:jira-token" \
  --remediation-mode approval \
  --ses-sender "notifications@company.com" \
  --ses-recipient "ops-team@company.com"
```

### Step 4: Verify end-to-end

After deployment, verify the classifier's EventBridge rule is subscribed to the AHA bus:

```bash
# Check the classifier's rule on the AHA bus
aws events list-rules --event-bus-name aha-eb01 --region eu-west-1 \
  --query "Rules[?contains(Name, 'aha-eventbridge')].{Name:Name,State:State}" --output table

# List targets for the rule (should point to the HealthEventFunction Lambda)
RULE_NAME=$(aws events list-rules --event-bus-name aha-eb01 --region eu-west-1 \
  --query "Rules[?contains(Name, 'aha-eventbridge')].Name" --output text)
aws events list-targets-by-rule --event-bus-name aha-eb01 --rule "$RULE_NAME" --region eu-west-1
```

### AHA event format differences

This classifier handles both event formats transparently — no configuration needed:

| Field | Native AWS Health | AHA-forwarded |
|---|---|---|
| EventBridge source | `aws.health` | `aha` |
| EventBridge bus | `default` | Custom (e.g., `aha-eb01`) |
| `detail` field | Dict | JSON string (auto-parsed) |
| `eventDescription` | List of dicts | Dict with `latestDescription` |
| `affectedEntities` | Basic entity info | Enriched with `awsAccountName`, `entityUrl` |

### Single-account vs. multi-account (AHA)

| Scenario | Event Source | Deploy Command |
|---|---|---|
| Single AWS account | Native `aws.health` on default bus | `./deploy.sh` |
| Multi-account organization with AHA | AHA on custom bus | `./deploy.sh --aha-event-bus "aha-eb01"` |
| Both (belt and suspenders) | Both sources | `./deploy.sh --aha-event-bus "aha-eb01"` (native events are always captured on the default bus) |

### Troubleshooting AHA integration

| Symptom | Likely Cause | Fix |
|---|---|---|
| No events received | AHA bus name is wrong | Run `aws events list-event-buses` and verify the name matches your `--aha-event-bus` value |
| Events received but Lambda errors | AHA event format mismatch | Check CloudWatch Logs for the HealthEventFunction Lambda — look for JSON parse errors in `event_parser.py` |
| Events processed but no classification | Events have status "closed" or "resolved" | The classifier only processes events with status "open" or "upcoming" — this is expected behavior |
| Duplicate notifications | Both native and AHA events arriving | If you have AHA, the native `aws.health` events on the default bus are also captured; deduplication is by event ARN in the agent |
| Lambda timeout (900s) | AgentCore Runtime cold start or model latency | First invocation may take 30-60s for container startup; subsequent invocations are faster |

### AHA prerequisites

- AHA deployed in your management account (or delegated admin account)
- AHA configured to forward events to a custom EventBridge bus
- This classifier deployed in the **same account and region** as AHA's EventBridge bus
- IAM permissions: the classifier's Lambda execution role needs no additional permissions for AHA — EventBridge invokes it directly via resource-based policy (configured by CloudFormation)

## Amazon Bedrock AgentCore Gateway + EKS MCP Server Integration (Optional)

The agent supports dynamic MCP tool discovery via AgentCore Gateway. When configured, the agent discovers tools from registered MCP servers (like the AWS managed EKS MCP Server) at startup and uses them for richer impact analysis.

### Setup Gateway with EKS MCP Server

```bash
# Install the starter toolkit
pip install bedrock-agentcore-starter-toolkit boto3

# Create Gateway and register EKS MCP Server as a target
python setup_gateway.py --region eu-west-1
```

This creates:
- A Cognito user pool for OAuth authorization
- An AgentCore Gateway with semantic search enabled
- The EKS MCP Server (`https://eks-mcp.{region}.api.aws/mcp`) registered as a target with SigV4 auth
- IAM permissions for `eks-mcp:*` and `eks:Describe*/List*` on the Gateway role

Configuration is saved to `gateway_config.json`.

### Deploy with Gateway

```bash
export AGENTCORE_GATEWAY_ENDPOINT="<gateway URL from setup_gateway.py output>"
./deploy.sh eu-west-1 update
```

When the Gateway endpoint is set, the agent:
1. Connects to the Gateway at startup via `MCPClient`
2. Discovers EKS MCP tools (e.g., `list_eks_resources`, `describe_eks_resource`, `get_eks_insights`)
3. Uses EKS MCP tools for EKS-related health notifications to confirm impact with real cluster data
4. Falls back to local tools if the Gateway is unavailable

The output JSON includes `gateway_status` ("connected"/"degraded"/"unavailable") and `discovered_mcp_tools` for observability.

### Cleanup Gateway

```bash
python setup_gateway.py --cleanup
```

### EKS MCP Server IAM Permissions

The Gateway role needs these permissions to call the EKS MCP Server (added automatically by `setup_gateway.py`):

```json
{
  "Effect": "Allow",
  "Action": [
    "eks-mcp:InvokeMcp",
    "eks-mcp:CallReadOnlyTool",
    "eks-mcp:CallPrivilegedTool"
  ],
  "Resource": "*"
}
```

> **Note:** The EKS MCP Server (preview) does not support resource-level permissions. `Resource: "*"` is required. Compensating controls: IAM role trust policy restricts which principals can assume this role, and the AgentCore Gateway Policy Engine (Cedar) enforces fine-grained tool-level authorization.

The EKS MCP Server is currently in preview. See [Introducing the fully managed Amazon EKS MCP Server](https://aws.amazon.com/blogs/containers/introducing-the-fully-managed-amazon-eks-mcp-server-preview/) for details.

#### EKS MCP Server Compliance

- **Legal Review:** Approved under AWS Service Terms for Preview Services (May 2026)
- **Right to Use:** Verified — AWS-managed service, no separate license required
- **Security:** SigV4 authentication, IAM-based access control
- **Data Handling:** Reads EKS cluster metadata only (versions, addons, insights). No customer application data accessed.

### MCP Tool Security Implementation

#### Priority 1: Authentication (Critical)

1. Configure OAuth 2.0 for MCP server authentication:
   ```bash
   aws cognito-idp create-user-pool --pool-name mcp-tool-auth
   aws cognito-idp create-user-pool-client --user-pool-id <pool-id> \
     --client-name mcp-eks-team --generate-secret
   ```
2. Measurable: 100% of MCP tool calls require valid OAuth token

#### Priority 2: Authorization (High)

3. Implement least-privilege IAM policies per team:
   ```json
   {
     "Effect": "Allow",
     "Action": "mcp:InvokeTool",
     "Resource": "arn:aws:mcp:*:*:tool/eks-team/*",
     "Condition": {"StringEquals": {"aws:PrincipalOrgID": "o-xxxxx"}}
   }
   ```
4. Measurable: Each team can only access their own MCP tools

#### Priority 3: Network Security (Medium)

5. Deploy MCP servers in private subnets with VPC endpoints
6. Measurable: Zero public internet exposure for MCP tool APIs

### MCP Server Approval Process

Before any MCP server can be registered, teams must:

1. Complete security review (authentication, authorization, data handling)
2. Obtain legal approval for any 3rd party dependencies
3. Verify data compliance with organizational classification policies
4. Submit approval request to security team

See `SECURITY.md` "3rd Party Service Approvals" for the approval template.
