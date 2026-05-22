# Integration Guide: Jira and Slack

## Jira Integration

When the agent confirms impact with remediation steps, a Jira ticket is created automatically (regardless of `REMEDIATION_MODE`):
- Issue type: `Task` (configurable via `JIRA_ISSUE_TYPE`)
- Team field (`customfield_10001`): set to the Jira Team ID from routing config
- Labels: classification, service, `phd-auto-created`, event ARN
- Priority: mapped from risk level (high → Highest, medium → High, low → Medium)
- Description: wiki markup with event details, classification, affected accounts, impact analysis, remediation steps

### Team Assignment Logic

Jira tickets are automatically assigned to the correct team using a multi-level routing priority chain. The system evaluates each level in order and uses the first match:

```
Priority 1: Resource name   (e.g., cluster "my-prod-cluster" → Team A)
Priority 2: Account ID      (e.g., account 111122223333 → Team B)
Priority 3: Service name    (e.g., EKS → Platform-Kubernetes team)
Priority 4: OU path         (e.g., Root/Production/Payments → Payments team)
Priority 5: Default         (fallback team for unmatched events)
```

Each mapping resolves to a **Jira Team ID** (UUID). This ID is set on `customfield_10001` in the created ticket, which Jira uses for team assignment in Team-managed and Company-managed projects.

### Finding your Jira Team IDs

1. In Jira, go to **People** → **Teams** (or your project's **Team settings**)
2. Click on a team → look at the URL: `https://myorg.atlassian.net/people/team/<team-id>`
3. The `<team-id>` is the UUID you need (e.g., `a1b2c3d4-e5f6-7890-abcd-ef1234567890`)

Alternatively, use the Jira REST API:
```bash
curl -s -u "email@company.com:$JIRA_API_TOKEN" \
  "https://myorg.atlassian.net/rest/teams/1.0/teams/find" \
  | python3 -m json.tool
```

### Configuring Team Routing

There are two ways to configure team routing, and they work together:

#### Option A: Static Configuration (deploy-time)

Pass the `JiraTeamMappings` CloudFormation parameter as a JSON string during deployment. This is useful for a fixed set of teams that rarely changes.

```bash
# Create the mappings JSON
TEAM_MAPPINGS='{"service_team_map":{"EKS":"uuid-platform-k8s","RDS":"uuid-platform-db"},"ou_team_map":{"Root/Production/Payments":"uuid-payments-team"},"account_team_map":{"111122223333":"uuid-team-a"},"resource_team_map":{"my-prod-cluster":"uuid-team-b"}}'

# Deploy (via CloudFormation parameter override)
aws cloudformation deploy \
  --stack-name aha-eventbridge-lambda \
  --template-file .build/packaged.yaml \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides JiraTeamMappings="$TEAM_MAPPINGS" \
  --region eu-west-1
```

**JSON structure:**
```json
{
  "service_team_map": {
    "EKS": "jira-team-uuid-1",
    "RDS": "jira-team-uuid-2",
    "LAMBDA": "jira-team-uuid-3"
  },
  "ou_team_map": {
    "Root/Production/Payments": "jira-team-uuid-4",
    "Root/Production/Platform": "jira-team-uuid-5"
  },
  "account_team_map": {
    "111122223333": "jira-team-uuid-6",
    "444455556666": "jira-team-uuid-7"
  },
  "resource_team_map": {
    "my-prod-cluster": "jira-team-uuid-8",
    "orders-database": "jira-team-uuid-9"
  }
}
```

#### Option B: Dynamic Configuration (auto-routing via S3 upload)

Upload a routing document to S3, and the system automatically parses it with Amazon Bedrock and writes the routing config to Secrets Manager. See [Auto Routing Config](../README.md#auto-routing-config) for the full flow.

This is the recommended approach for organizations where team ownership changes frequently.

#### How the two options merge

At runtime, the system loads configuration from both sources. **Secrets Manager values take precedence** over the CloudFormation parameter:

```
1. Lambda starts → reads JIRA_TEAM_MAPPINGS env var (from CloudFormation parameter)
2. Lambda creates JiraClient → reads Secrets Manager secret (same ARN as Jira API token)
3. If Secrets Manager contains routing keys, they OVERWRITE the env var values
4. Final merged config is used for team resolution
```

This means:
- You can bootstrap with a static `JiraTeamMappings` parameter at deploy time
- Then switch to dynamic management by uploading routing documents to S3
- Once Secrets Manager has routing data, the CloudFormation parameter is effectively ignored for those keys
- The Jira API token and routing config share the same Secrets Manager secret

#### Example: Secrets Manager secret structure

The Jira secret in Secrets Manager contains both the API token and routing maps:

```json
{
  "jira_api_token": "ATATT3xFfG...",
  "service_team_map": {"EKS": "uuid-1", "RDS": "uuid-2"},
  "ou_team_map": {"Root/Production": "uuid-3"},
  "account_team_map": {"111122223333": "uuid-4"},
  "resource_team_map": {"my-cluster": "uuid-5"},
  "default_assignee": "uuid-fallback"
}
```

### Routing Resolution Examples

| Health Event | Affected Resource | Affected Account | Service | Account OU | Resolved Team |
|---|---|---|---|---|---|
| EKS upgrade needed | `my-prod-cluster` | 111122223333 | EKS | Root/Production | **uuid-5** (resource match — highest priority) |
| RDS deprecation | (none listed) | 444455556666 | RDS | Root/Staging | **uuid-7** (account match) |
| Lambda runtime EOL | (none listed) | 777788889999 | LAMBDA | Root/Production | **uuid-3** (service match) |
| Keyspaces TLS change | (none listed) | 999900001111 | CASSANDRA | Root/Production/Payments | **uuid-4** (OU match from Secrets Manager) |
| Unknown service event | (none listed) | 222233334444 | NEWSERVICE | Root/Dev | **uuid-fallback** (default) |

### Ticket Creation Conditions

Jira tickets are only created when ALL of these conditions are met:
1. Jira integration is configured (all required env vars present)
2. Agent classifies the notification and returns `impact_analysis`
3. `impact_status` is `"confirmed"`
4. `suggested_next_steps` is non-empty (there are remediation actions)
5. No duplicate ticket exists (checked by event ARN label)

If any condition is not met, no ticket is created and no error is raised.

## Slack Integration

This system uses Slack in two ways, depending on which features you enable:

| Feature | Slack Mechanism | Required Config | When Used |
|---|---|---|---|
| Health event notifications | Slack Workflow webhook (outbound only) | `--slack-webhook` | Agent classifies a SERVICE_DISRUPTION, BREAKING_CHANGE, or SECURITY_RELATED event |
| Routing config approval | Slack Workflow webhook + Slack App (interactive) | `--slack-webhook` + `SlackSigningSecret` parameter | `REQUIRE_ROUTING_APPROVAL=true` and a routing document is uploaded to S3 |

### Option A: Health Event Notifications Only (simplest)

Posts a one-way notification to Slack when the agent classifies an actionable health event. No interactive buttons, no Slack App needed.

**Step 1: Create a Slack Workflow with a webhook trigger**

1. In Slack, go to **Automations** → **New Workflow** → **From a webhook**
2. Add these variables to the webhook trigger (all type: Text):
   - `source_file` — contains the notification subject (e.g., "[BREAKING_CHANGE] EKS — action required")
   - `routing_json` — contains the plain-text classification summary
   - `summary` — contains the event ARN
   - `approval_url` — contains the approval URL (empty for notification-only mode)
3. Add a **Send a message** step that formats these variables however you like
4. **Publish** the workflow and copy the webhook URL (starts with `https://hooks.slack.com/triggers/...`)

**Step 2: Deploy with the webhook URL**

```bash
./deploy.sh --slack-webhook "https://hooks.slack.com/triggers/T.../..."
```

That's it. Health event notifications will appear in the channel configured by your Slack Workflow.

### Option B: Routing Config Approval via Slack (interactive)

Adds interactive approval buttons when routing config documents are uploaded to S3. Requires a Slack App for request signature verification.

**Step 1: Create a Slack App for interactive payloads**

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. Name it (e.g., "PHD Routing Approvals") and select your workspace
3. Under **Basic Information** → **App Credentials**, copy the **Signing Secret**
4. Under **Interactivity & Shortcuts**, toggle ON and set the Request URL to:
   ```
   https://<your-api-gateway-url>/prod/slack/interactive
   ```
   (You'll get this URL from the CloudFormation stack outputs after first deploy — you can update it later)

**Step 2: Create the Slack Workflow (same as Option A)**

Follow the same steps as Option A above. The approval workflow reuses the same webhook URL but includes an `approval_url` value that your message template can render as a button/link.

**Step 3: Deploy with webhook + signing secret**

```bash
./deploy.sh \
  --slack-webhook "https://hooks.slack.com/triggers/T.../..." \
  --require-routing-approval
```

Then set the Slack Signing Secret as a CloudFormation parameter (it's `NoEcho` so it won't be displayed):

```bash
aws cloudformation update-stack \
  --stack-name aha-eventbridge-lambda \
  --use-previous-template \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameters \
    ParameterKey=SlackSigningSecret,ParameterValue="your-signing-secret" \
    ParameterKey=AgentContainerUri,UsePreviousValue=true \
    ParameterKey=SlackWebhookUrl,UsePreviousValue=true \
    ParameterKey=RequireRoutingApproval,UsePreviousValue=true \
  --region eu-west-1
```

**Step 4: Update the Slack App Interactivity URL**

After deployment, get the API Gateway URL from stack outputs:

```bash
aws cloudformation describe-stacks --stack-name aha-eventbridge-lambda \
  --region eu-west-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ApprovalApiUrl`].OutputValue' --output text
```

Set this as the Interactivity Request URL in your Slack App settings:
```
https://<api-gateway-id>.execute-api.eu-west-1.amazonaws.com/prod/slack/interactive
```

### Slack payload format reference

The webhook sends JSON with these fields:

```json
{
  "source_file": "[BREAKING_CHANGE] EKS — AWS_EKS_PERSISTENT_CLUSTER_VERSION_UPGRADE",
  "routing_json": "EKS breaking change affects 3 account(s) (2 production, 1 non-production).\nAffected: cluster-prod-1, cluster-staging-2\nDeadline: 2026-07-23",
  "summary": "arn:aws:health:us-east-1::event/EKS/...",
  "approval_url": ""
}
```

For routing config review (with `REQUIRE_ROUTING_APPROVAL=true`):

```json
{
  "source_file": "routing.txt",
  "routing_json": "{\n  \"by_service\": {\"EKS\": \"team-uuid-1\"},\n  \"by_account\": {},\n  \"by_ou\": {},\n  \"by_resource\": {},\n  \"default\": \"team-uuid-2\"\n}",
  "summary": "1 service mappings, 0 OU mappings, default: team-uuid-2",
  "approval_url": "https://<api-gw>/prod/approve-routing?token=abc123..."
}
```

### Troubleshooting Slack

| Symptom | Likely Cause | Fix |
|---|---|---|
| No Slack notifications | Webhook URL not configured or empty | Verify `SLACK_WEBHOOK_URL` env var is set in the Lambda (check CloudFormation parameters) |
| Webhook returns 403 | Workflow not published or URL expired | Republish the Slack Workflow; webhook URLs can expire if the workflow is unpublished |
| Webhook returns 400 | Payload field names don't match workflow variables | Ensure your Slack Workflow has variables named exactly: `source_file`, `routing_json`, `summary`, `approval_url` |
| Interactive approval fails | Signing secret mismatch or not set | Verify `SlackSigningSecret` matches your Slack App's signing secret |
| Interactive approval 401 | Request too old (>5 min) | Clock skew between Slack and Lambda; check Lambda is in a region with low latency to Slack |
| Only SERVICE_DISRUPTION/BREAKING_CHANGE/SECURITY_RELATED posted | By design | COST_IMPLICATION and INFORMATIONAL events are not posted to Slack (only to SNS) |
