# Design Document: PHD Notification Classifier

## Overview

This design describes an AI agent built with the Strands Agents SDK and deployed on Amazon Bedrock AgentCore Runtime. The agent uses an LLM (Claude) to consolidate, classify, and analyze unclosed AWS Personal Health Dashboard (PHD) notifications across the organization. The agent receives health event payloads from the aha-eventbridge-lambda Lambda function via the `@app.entrypoint` decorator — it does not fetch health events directly. Before classification, the agent enriches each event with account context from AWS Organizations (account name, OU membership, account tags) to determine environment type. After classification and impact analysis, the agent publishes a structured notification summary to a configured SNS topic for downstream consumption.

Notifications are classified into six categories with a defined priority ordering:
- **SERVICE_DISRUPTION** — active outages, regional disruptions, or ongoing service degradation currently impacting workloads
- **BREAKING_CHANGE** — service deprecations, API removals, endpoint retirements, or any change that will cause workloads to stop functioning without customer action
- **SECURITY_RELATED** — security vulnerabilities, compliance issues, or security patches
- **COST_IMPLICATION** — extended support fees, pricing changes, or resource cost increases
- **INFORMATIONAL** — no action required (renewals, credits, resolved events, general advisories)
- **UNCLASSIFIED** — insufficient detail to assign a meaningful classification

Priority rule: SERVICE_DISRUPTION > BREAKING_CHANGE > SECURITY_RELATED > COST_IMPLICATION > INFORMATIONAL > UNCLASSIFIED

Each notification also receives an **urgency** level (critical/high/medium/low) based on deadline proximity, and the agent extracts action **deadlines** from notification text.

The agent consolidates related notifications across accounts into unified views with production/non-production breakdowns, performs breaking change impact analysis with environment-based risk scoring, produces cost projections for cost-related notifications, and publishes structured summaries to an SNS topic for downstream systems to consume.

**Dependencies:** `strands-agents`, `strands-agents-tools` (MCP client), `bedrock-agentcore`, `boto3`, `mcp` (StdioServerParameters), `hypothesis` (test), `pytest` (test)

### Key Design Decisions

1. **LLM-based classification over rule-based keyword matching**: PHD notification descriptions are natural language with significant variation. An LLM can understand context, nuance, and novel phrasing that keyword matching would miss. The six-category classification (SERVICE_DISRUPTION, BREAKING_CHANGE, SECURITY_RELATED, COST_IMPLICATION, INFORMATIONAL, UNCLASSIFIED) with priority ordering is encoded in the system prompt.

2. **Event ingestion via aha-eventbridge-lambda, not direct fetch**: The agent receives health event payloads pushed by the aha-eventbridge-lambda Lambda function. This decouples event sourcing from classification — AHA handles EventBridge integration, the Lambda invokes AgentCore, and the agent focuses on enrichment, classification, and analysis. There is no `fetch_phd_notifications` tool.

3. **Account context enrichment before classification**: The `get_account_context` tool calls AWS Organizations APIs to retrieve account name, OU membership, and tags for each affected account. This enrichment happens before classification so the agent has full environment context (production vs non-production) for accurate impact analysis and risk scoring.

4. **SNS topic for downstream notification**: Rather than building direct integrations for Jira, Teams, or Slack, the agent publishes structured JSON summaries to a configured SNS topic. Downstream systems subscribe to the topic and take action independently. This decouples the agent from specific action systems and allows flexible fan-out.

5. **Consolidation before classification**: Related notifications across accounts are grouped into consolidated views before classification. This ensures the LLM sees the full organizational picture and avoids duplicate classification work.

6. **Separate tool functions for each capability**: Each major capability (get account context, get application trust store, consolidate, classify, analyze impact, estimate cost, publish to SNS) is a separate `@tool` function. This gives the LLM fine-grained control over the workflow and makes each component independently testable.

7. **Environment-based risk scoring**: The Impact_Analyzer assigns higher risk to production environments than non-production. Environment type is determined from account context (tags or OU membership) retrieved by `get_account_context`.

8. **Structured JSON output via prompt engineering**: The system prompt instructs the agent to return a specific JSON schema that includes classification, affected accounts, cost projections, impact analysis, environment breakdowns, and SNS publish status.

## Architecture

```mermaid
flowchart TD
    AHA[AHA<br/>AWS Health Aware] -->|EventBridge| EB[Amazon EventBridge]
    EB -->|Rule trigger| Lambda[aha-eventbridge-lambda]
    Lambda -->|POST /invocations| A[AgentCore Runtime<br/>HTTP :8080]
    A -->|@app.entrypoint| C[Agent Entrypoint]
    C -->|prompt + payload| D[Strands Agent<br/>Claude on Bedrock]

    D -->|tool call| AC["@tool get_account_context"]
    D -->|tool call| APPCTX["@tool get_account_application_trust_store"]
    D -->|tool call| F["@tool consolidate_notifications"]
    D -->|tool call| G["@tool analyze_impact"]
    D -->|tool call| H["@tool estimate_cost"]
    D -->|tool call| EKS_DESC["@tool describe_eks_cluster"]
    D -->|tool call| EKS_UPG["@tool upgrade_eks_cluster"]
    D -->|tool call| SNS["@tool publish_to_sns"]

    AC -->|boto3| ORG[AWS Organizations API]
    ORG -->|account name, OU, tags| AC
    AC -->|account context| D
    APPCTX -->|DynamoDB/JSON<br/>application context| D
    F -->|consolidated views| D
    G -->|impact analysis| D
    H -->|cost projections| D
    EKS_DESC -->|boto3| EKSAPI[Amazon EKS API]
    EKS_UPG -->|boto3| EKSAPI
    SNS -->|boto3| SNST[Amazon SNS Topic]

    D -->|MCP stdio| MCP[awslabs.eks-mcp-server<br/>v0.1.3]

    D -->|LLM orchestration<br/>+ structured JSON| C
    C -->|response| A
    A -->|JSON response| Lambda

    subgraph Agent Process
        A
        subgraph Application Logic
            C
            D
            AC
            APPCTX
            F
            G
            H
            SNS
        end
    end

    subgraph External Services
        ORG
        SNST
    end

    subgraph Event Pipeline
        AHA
        EB
        Lambda
    end
```

The agent runs as a `BedrockAgentCoreApp` instance. When invoked by the aha-eventbridge-lambda, the entrypoint receives the health event payload and creates a Strands `Agent` to orchestrate the workflow:

1. aha-eventbridge-lambda receives an AWS Health event from EventBridge (forwarded by AHA) and invokes the AgentCore Runtime endpoint
2. The `@app.entrypoint` function receives the health event payload and constructs a prompt for the Strands Agent
3. The LLM calls `get_account_context` for each affected account to retrieve account name, OU membership, and tags
4. The LLM calls `consolidate_notifications` to group related notifications across accounts (with enriched account context)
5. The LLM classifies each consolidated notification using criteria from its system prompt
6. When the notification's impact depends on application-level configuration (e.g., TLS trust stores), the LLM calls `get_account_application_trust_store` to retrieve application context for affected accounts
7. For BREAKING_CHANGE notifications, the LLM calls `analyze_impact` to assess affected accounts and resources, using application context to determine confirmed vs unconfirmed impact
8. For COST_IMPLICATION notifications, the LLM calls `estimate_cost` to produce cost projections
9. The LLM calls `publish_to_sns` to publish the structured notification summary to the configured SNS topic
10. The entrypoint extracts and returns the structured JSON response

## Components and Interfaces

### 1. Agent Entry Point (`agent.py`)

The agent is built using `BedrockAgentCoreApp` from `bedrock-agentcore` and `Agent` from `strands`. The entrypoint receives a health event payload from the aha-eventbridge-lambda, constructs a Strands Agent with the system prompt and all tools, and streams the response. The system prompt is loaded from S3 at cold start (with fallback to embedded), the model ID is configurable via `BEDROCK_MODEL_ID` env var, and EKS cluster tools are included as local boto3 `@tool` functions. An optional MCP server (`awslabs.eks-mcp-server@0.1.3`) is connected via stdio when `EKS_MCP_ENABLED=true`.

```python
from strands import Agent
from bedrock_agentcore import BedrockAgentCoreApp
from tools.account_context import get_account_context
from tools.application_context import get_account_application_trust_store
from tools.consolidation import consolidate_notifications
from tools.impact_analyzer import analyze_impact
from tools.cost_estimator import estimate_cost
from tools.eks_cluster import describe_eks_cluster, upgrade_eks_cluster
from tools.sns_notifier import publish_to_sns
from gateway_tools import discover_mcp_tools
from prompts import SYSTEM_PROMPT

app = BedrockAgentCoreApp()

# MCP tool discovery (optional, direct EKS MCP server via stdio)
mcp_tools = discover_mcp_tools()

local_tools = [
    get_account_context,
    consolidate_notifications,
    analyze_impact,
    estimate_cost,
    describe_eks_cluster,
    upgrade_eks_cluster,
    publish_to_sns,
]
if not mcp_tools:
    local_tools.append(get_account_application_trust_store)
all_tools = local_tools + mcp_tools

DEFAULT_MODEL_ID = "eu.anthropic.claude-sonnet-4-6"

def _load_system_prompt() -> str:
    """Load system prompt from S3 if configured, otherwise use embedded default."""
    bucket = os.environ.get("SYSTEM_PROMPT_S3_BUCKET")
    key = os.environ.get("SYSTEM_PROMPT_S3_KEY", "prompts/system_prompt.txt")
    if not bucket:
        return SYSTEM_PROMPT
    try:
        import boto3
        s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        response = s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read().decode("utf-8")
    except Exception:
        return SYSTEM_PROMPT

_system_prompt = _load_system_prompt()

agent = Agent(
    model=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
    system_prompt=_system_prompt,
    tools=all_tools,
)

@app.entrypoint
async def classify_notifications(payload):
    """Stream the agent response, yielding only the final result text."""
    prompt = build_prompt(payload)
    async for event in agent.stream_async(prompt):
        if "result" in event:
            result = event["result"]
            message = result.message if hasattr(result, "message") else {}
            content = message.get("content", []) if isinstance(message, dict) else []
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict) and "text" in c]
            yield "\n".join(text_parts)

def build_prompt(payload):
    """Build the agent prompt from the health event payload."""
    # Extract the health event data from the payload
    health_event = payload.get("health_event", payload)
    limit = payload.get("limit")
    prompt = f"Process the following AWS Health event payload and classify it:\n\n{health_event}"
    if limit and int(limit) > 0:
        prompt += f"\n\nLimit processing to {int(limit)} notifications."
    return prompt

if __name__ == "__main__":
    app.run()
```

**Responsibilities:**
- Configure the Strands Agent with the system prompt (loaded from S3 or embedded fallback) and all tools (local + MCP)
- Receive health event payloads from the aha-eventbridge-lambda via `@app.entrypoint`
- Validate the incoming payload and construct a prompt for the agent
- Filter the Strands streaming response to yield only the final result text (the "result" event), not the full streaming trace — downstream consumers receive a clean, manageable response
- The agent module-level initialization creates the Agent once with local tools (get_account_context, get_account_application_trust_store, consolidate_notifications, analyze_impact, estimate_cost, describe_eks_cluster, upgrade_eks_cluster, publish_to_sns) plus any MCP tools discovered; the entrypoint reuses it per invocation
- The Bedrock model ID is read from `BEDROCK_MODEL_ID` env var with a default of `eu.anthropic.claude-sonnet-4-6`
- System prompt loaded from S3 (`SYSTEM_PROMPT_S3_BUCKET`/`SYSTEM_PROMPT_S3_KEY`) at cold start, falling back to embedded default
- MCP tools discovered via direct stdio connection to `awslabs.eks-mcp-server@0.1.3` when `EKS_MCP_ENABLED=true`

**Runtime behavior:**
- `app.run()` starts an HTTP server on port 8080
- `/invocations` — routes to the `@app.entrypoint` function
- `/ping` — built-in health check

### 2. Account Context Tool (`tools/account_context.py`)

A Strands `@tool` decorated function that retrieves account context from AWS Organizations for affected accounts.

```python
@tool
def get_account_context(account_id: str) -> dict:
    """Retrieve account context from AWS Organizations for a given account.
    Calls describe_account, list_parents, and list_tags_for_resource to return
    the account name, OU membership path, and account tags.
    Returns a dict with account_name, ou_path, tags, and environment_type."""
```

**Key behaviors:**
- Calls `describe_account` to get the account name
- Calls `list_parents` (recursively) to build the full OU membership path
- Calls `list_tags_for_resource` to retrieve account tags
- Determines `environment_type` (production or non-production) from account tags or OU membership
- Returns a dict with `account_id`, `account_name`, `ou_path`, `tags`, and `environment_type`
- On AWS Organizations API failure, logs the error and returns a fallback dict with `environment_type` set to `"unknown"`

### 3. Application Context Tool (`tools/application_context.py`)

A Strands `@tool` decorated function that retrieves application-level context (e.g., trust store information) for affected accounts. Reads from DynamoDB or static JSON configuration.

```python
@tool
def get_account_application_trust_store(account_id: str) -> dict:
    """Retrieve application trust store and deployment context for a given account.
    Data sources (checked in order):
    1. DynamoDB table (APP_CONTEXT_TABLE_NAME env var)
    2. Static JSON config (APP_CONTEXT_JSON env var)
    3. Empty response (indicates data source not configured)
    Returns a dict with account_id, trust_store, and applications."""
```

**Key behaviors:**
- Accepts an AWS account ID and returns application-level trust store and deployment information
- Attempts to read from DynamoDB first (`APP_CONTEXT_TABLE_NAME` env var), then falls back to static JSON (`APP_CONTEXT_JSON` env var)
- Returns a dict with `account_id`, `trust_store` (a list of trusted certificate authority names), and `applications` (list of application details with trust store and deployment info)
- The agent calls this tool when a notification's impact depends on application-level configuration (e.g., TLS certificate changes) to determine whether the impact is confirmed or unconfirmed
- If neither data source is configured, returns empty `trust_store` and `applications` lists, and the agent treats the impact as unconfirmed

### 4. Notification Consolidation Tool (`tools/consolidation.py`)

Groups related notifications across accounts into consolidated views.

```python
@tool
def consolidate_notifications(notifications: list) -> list:
    """Consolidate related notifications across accounts into unified views.
    Groups by event ARN/type, provides account-level detail and org-wide summary.
    Categorizes impact separately for production and non-production environments."""
```

**Key behaviors:**
- Groups notifications by the same health event (matching event ARN or event type code + service)
- Produces a consolidated view per unique event with account-level detail
- Categorizes affected accounts as production or non-production based on enriched account context
- Updates existing consolidated views when new related notifications arrive rather than creating duplicates
- Returns consolidated views with org-wide impact summaries

### 5. Impact Analyzer Tool (`tools/impact_analyzer.py`)

Assesses breaking change impact with environment-based risk scoring.

```python
@tool
def analyze_impact(notification: dict, affected_accounts: list) -> dict:
    """Analyze the impact of a BREAKING_CHANGE notification.
    Inspects affected accounts/resources, assigns risk based on environment type,
    and produces an impact summary with required actions."""
```

**Key behaviors:**
- Inspects all affected accounts and resources for a BREAKING_CHANGE notification
- Assigns higher risk scores to production environments than non-production
- Produces an impact summary listing each affected account, resources, and required actions
- Preserves the full event description in the impact summary without truncation — downstream formatting handles any length constraints
- Returns "no action required" when no affected resources are found in any account
- Includes an `impact_status` field: `"confirmed"` when the agent has enough information to definitively determine resources are impacted, or `"unconfirmed"` when the agent cannot confirm actual impact based on available information
- Always includes a `suggested_next_steps` list of specific, actionable steps tailored to the notification type and affected service:
  - When `impact_status` is `"confirmed"`: contains remediation steps to address the confirmed issue (e.g., "Update your trust store to include Amazon Root CA 1", "Test connectivity after updating certificates")
  - When `impact_status` is `"unconfirmed"`: contains verification steps the operator should take to determine the actual impact (e.g., "Check if your applications use custom TLS trust stores", "Verify if Starfield C2 is the only trusted CA in your trust store")

### 6. Cost Estimator Tool (`tools/cost_estimator.py`)

Produces cost projections for COST_IMPLICATION notifications.

```python
@tool
def estimate_cost(notification: dict, affected_accounts: list) -> dict:
    """Estimate cost impact for a COST_IMPLICATION notification.
    Projects per-account and org-wide costs. Tracks historical data for accuracy.
    Returns 'unknown' with reason if projection cannot be determined."""
```

**Key behaviors:**
- Produces projected cost impact per affected account
- Aggregates per-account costs into an organization-wide total
- Tracks historical cost data for similar events to improve projection accuracy
- Returns "unknown" with reason when cost projection cannot be determined

### 7. SNS Notifier Tool (`tools/sns_notifier.py`)

Publishes structured notification summaries to the configured SNS topic.

```python
@tool
def publish_to_sns(notification_summary: dict) -> dict:
    """Publish a structured notification summary to the configured SNS topic.
    Reads SNS_TOPIC_ARN from environment variable. Includes classification,
    impact analysis, cost projections, and affected accounts as structured JSON.
    Returns publish result or failure details."""
```

**Key behaviors:**
- Reads the SNS topic ARN from the `SNS_TOPIC_ARN` environment variable
- Extracts the AWS region from the topic ARN (e.g., `arn:aws:sns:eu-west-1:...` → `eu-west-1`) and passes it to `boto3.client("sns", region_name=region)` — the AgentCore container may not have `AWS_DEFAULT_REGION` set, so the region must always be specified explicitly
- Publishes a structured JSON payload containing: notification identifier, event type, affected service, classification, classification reason, impact analysis, cost projections, and affected accounts
- If `SNS_TOPIC_ARN` is not set, logs a warning and skips publishing (returns `{"status": "skipped", "reason": "SNS_TOPIC_ARN not configured"}`)
- If the SNS publish call fails, logs the failure and returns `{"status": "failed", "error": "<details>"}` — the failure details are included in the agent output
- Uses boto3 SNS client to call `publish()`

### 8. Messaging Notifier (`tools/messaging_notifier.py`)

Sends consolidated impact summaries to Microsoft Teams and Slack via AHA's webhook integrations. Builds a human-readable text message from the agent's classification output.

```python
@tool
def send_teams_notification(summary: dict) -> dict:
    """Send consolidated impact summary to Microsoft Teams via AHA's webhook."""

@tool
def send_slack_notification(summary: dict) -> dict:
    """Send consolidated impact summary to Slack via AHA's webhook."""
```

**Key behaviors:**
- Builds a structured text message from the agent's full output dict containing `notifications`
- For each notification, includes classification, service, reason, affected accounts, and action required
- When impact analysis includes `suggested_next_steps`, displays them with a context-appropriate header:
  - When `impact_status` is `"confirmed"`: displays under the header "Suggested Remediation Steps:"
  - When `impact_status` is `"unconfirmed"`: displays under the header "Suggested Verification Steps:"
- Includes actionable links to the AWS Health console and Jira ticket (if available)
- Uses pluggable AHA webhook callables for Teams and Slack delivery
- Returns `{"status": "sent"}` on success or `{"status": "failed", "error": "..."}` on failure

### 9. System Prompt

The system prompt encodes the classification logic, workflow orchestration, and output format.

```text
You are a PHD (Personal Health Dashboard) notification classifier and analysis agent for AWS.

You receive health event payloads from the aha-eventbridge-lambda Lambda function.
When invoked with a health event payload, follow this workflow:

1. Parse the health event payload to extract notification details and affected accounts
2. Use get_account_context for each affected account to retrieve account name, OU membership, and tags
3. Use consolidate_notifications to group related notifications across accounts
4. Classify each consolidated notification using the rules below
5. Use get_account_application_trust_store to get application information for impact analysis if it is an application certificate related issue
6. For BREAKING_CHANGE notifications, use analyze_impact to assess affected resources
   - Determine whether the impact is CONFIRMED or UNCONFIRMED:
     - CONFIRMED: You have enough information from tools, account context, or the notification itself to definitively say the resources ARE impacted
     - UNCONFIRMED: You cannot confirm whether the resources are actually impacted based on available information (e.g., the notification affects a service the account uses, but you cannot verify application-level configuration)
   - Always generate suggested_next_steps specific to the notification type and affected service — do not use generic steps:
     - When impact is CONFIRMED: generate remediation steps to address the confirmed issue (e.g., "Update your trust store to include Amazon Root CA 1", "Test connectivity after updating certificates")
     - When impact is UNCONFIRMED: generate verification steps the operator should take to determine the actual impact (e.g., "Check if your applications use custom TLS trust stores")
7. For COST_IMPLICATION notifications, use estimate_cost to project financial impact
8. Use publish_to_sns to publish the structured notification summary to the SNS topic
9. Return the structured JSON result

## Classification Rules

Classify each notification into exactly one category:

**SERVICE_DISRUPTION** — The notification describes an active outage, regional disruption,
or ongoing service degradation that is currently impacting workloads.

**BREAKING_CHANGE** — The notification describes a change that will cause existing
workloads to stop functioning without customer action. This includes:
- Service deprecations, API removals, endpoint retirements
- End-of-life changes that break connectivity or functionality
- Certificate changes that may cause connection failures
- Any change where inaction leads to service disruption

**SECURITY_RELATED** — The notification describes a security concern. This includes:
- Security vulnerabilities requiring patches
- Compliance issues requiring attention
- Security patches or updates
- Any change involving security risk

**COST_IMPLICATION** — The notification describes a financial impact. This includes:
- Resources approaching end-of-standard-support with paid extended support
- Version upgrades needed to avoid additional charges
- Pricing changes or resource cost increases
- Any change where inaction leads to increased costs but not breakage

**INFORMATIONAL** — The notification requires no customer action. This includes:
- Renewals, credits, resolved events
- General advisories with no action needed
- Status updates on previously resolved issues

**UNCLASSIFIED** — The notification has insufficient detail to assign a meaningful
classification. Use this when the event description is too vague or ambiguous.

## Priority Rule
Evaluate in order: SERVICE_DISRUPTION > BREAKING_CHANGE > SECURITY_RELATED > COST_IMPLICATION > INFORMATIONAL > UNCLASSIFIED.
Each notification receives exactly one classification.

## Urgency Assignment
Assign urgency based on deadline proximity:
- critical: deadline within 7 days
- high: deadline within 30 days
- medium: deadline within 90 days
- low: deadline beyond 90 days or no deadline extractable

## Output Format
Return a JSON object with this structure:
{
  "status": "success",
  "notifications": [
    {
      "notification_id": "<event ARN>",
      "classification": "SERVICE_DISRUPTION" | "BREAKING_CHANGE" | "SECURITY_RELATED" | "COST_IMPLICATION" | "INFORMATIONAL" | "UNCLASSIFIED",
      "urgency": "critical" | "high" | "medium" | "low",
      "deadline": "<extracted date or null>",
      "reason": "<explanation referencing specific notification details>",
      "event_type": "<eventTypeCode>",
      "affected_service": "<service name>",
      "affected_accounts": [
        {
          "account_id": "<account>",
          "account_name": "<name>",
          "environment_type": "production" | "non-production" | "unknown",
          "affected_resources": ["<resource ARNs>"]
        }
      ],
      "environment_breakdown": {
        "production_count": <number>,
        "non_production_count": <number>
      },
      "impact_analysis": {
        "action_required": true | false,
        "risk_level": "high" | "medium" | "low",
        "impact_status": "confirmed" | "unconfirmed",
        "summary": "<impact summary>",
        "suggested_next_steps": ["<step 1>", "<step 2>", ...]
      } | null,
      "cost_projection": { ... } | null
    }
  ],
  "total_count": <number>,
  "service_disruption_count": <number>,
  "breaking_change_count": <number>,
  "security_related_count": <number>,
  "cost_implication_count": <number>,
  "informational_count": <number>,
  "unclassified_count": <number>,
  "sns_publish_status": "sent" | "failed" | "skipped"
}
```

## Data Models

### Health Event Payload (input from aha-eventbridge-lambda)

The agent receives health event payloads via `@app.entrypoint`. The payload structure mirrors the AWS Health event forwarded by AHA through EventBridge:

```python
{
    "arn": str,                    # Event ARN (unique identifier)
    "service": str,                # e.g., "CASSANDRA", "RDS", "EKS"
    "eventTypeCode": str,          # e.g., "AWS_CASSANDRA_PLANNED_LIFECYCLE_EVENT"
    "eventTypeCategory": str,      # "accountNotification", "scheduledChange", "issue"
    "statusCode": str,             # "open" or "upcoming" (closed are filtered out)
    "region": str,                 # AWS region
    "eventDescription": str,       # Human-readable description text
    "affectedAccounts": list[str], # List of affected AWS account IDs
}
```

### Account Context (from get_account_context)

```python
{
    "account_id": str,             # AWS account ID
    "account_name": str,           # Human-readable account name
    "ou_path": str,                # Full OU membership path, e.g., "Root/Production/US-East"
    "tags": dict[str, str],        # Account tags as key-value pairs
    "environment_type": str,       # "production", "non-production", or "unknown"
}
```

### Consolidated View

```python
{
    "event_key": str,              # Unique key for the consolidated event
    "event_arns": list[str],       # All related event ARNs
    "service": str,                # Affected AWS service
    "eventTypeCode": str,          # Event type code
    "eventDescription": str,       # Shared description
    "affected_accounts": [
        {
            "account_id": str,
            "account_name": str,
            "environment_type": str,   # "production", "non-production", or "unknown"
            "affected_resources": list[str],
        }
    ],
    "environment_breakdown": {
        "production_count": int,
        "non_production_count": int,
    },
    "org_impact_summary": str,     # Organization-wide impact summary
}
```

### Impact Analysis (from Impact_Analyzer)

```python
{
    "notification_id": str,
    "action_required": bool,
    "risk_level": str,             # "high", "medium", "low" based on environment types
    "impact_status": str,          # "confirmed" or "unconfirmed"
    "affected_accounts": [
        {
            "account_id": str,
            "environment_type": str,
            "affected_resources": list[str],
            "required_action": str,
        }
    ],
    "summary": str,                # Human-readable impact summary
    "suggested_next_steps": list[str],  # Always present; remediation steps when confirmed, verification steps when unconfirmed
}
```

### Cost Projection (from Cost_Estimator)

```python
{
    "notification_id": str,
    "projectable": bool,
    "per_account_costs": [
        {
            "account_id": str,
            "projected_cost": float | None,
            "currency": str,
        }
    ],
    "org_total_projected_cost": float | None,
    "currency": str,
    "reason": str | None,          # Reason if cost is unknown
    "historical_reference": str | None,  # Reference to similar past events
}
```

### SNS Notification Payload (published by SNS_Notifier)

```python
{
    "notification_id": str,        # Event ARN
    "event_type": str,             # eventTypeCode
    "affected_service": str,       # Service name
    "classification": str,         # BREAKING_CHANGE | COST_IMPLICATION | SECURITY_RELATED
    "reason": str,                 # Classification reason
    "affected_accounts": list,     # Account details with environment type
    "impact_analysis": dict | None,
    "cost_projection": dict | None,
}
```

### Output JSON Schema

#### Example: Unconfirmed Impact (with verification steps)

```json
{
  "status": "success",
  "notifications": [
    {
      "notification_id": "arn:aws:health:...",
      "classification": "BREAKING_CHANGE",
      "reason": "This notification describes a breaking change: Amazon Keyspaces will no longer include Starfield C2 in its certificate chain, which may cause applications to fail to connect.",
      "event_type": "AWS_CASSANDRA_PLANNED_LIFECYCLE_EVENT",
      "affected_service": "CASSANDRA",
      "affected_accounts": [
        {
          "account_id": "123456789012",
          "account_name": "prod-us-east",
          "environment_type": "production",
          "affected_resources": ["arn:aws:cassandra:us-east-1:123456789012:keyspace/mykeyspace"]
        }
      ],
      "environment_breakdown": {
        "production_count": 1,
        "non_production_count": 2
      },
      "impact_analysis": {
        "action_required": true,
        "risk_level": "high",
        "impact_status": "unconfirmed",
        "summary": "Production account 123456789012 has 1 affected Keyspaces resource. Impact is unconfirmed — the agent cannot determine whether the application's trust store exclusively trusts Starfield C2.",
        "suggested_next_steps": [
          "Check if your applications use custom TLS trust stores when connecting to Keyspaces",
          "Verify if Starfield C2 is the only trusted CA in your trust store",
          "Test connectivity with Amazon Root CA 1 in your trust store",
          "Review your application's TLS configuration and certificate pinning settings"
        ]
      },
      "cost_projection": null
    }
  ],
  "total_count": 1,
  "breaking_change_count": 1,
  "cost_implication_count": 0,
  "security_related_count": 0,
  "sns_publish_status": "sent"
}
```

#### Example: Confirmed Impact (with remediation steps)

```json
{
  "status": "success",
  "notifications": [
    {
      "notification_id": "arn:aws:health:...",
      "classification": "BREAKING_CHANGE",
      "reason": "This notification describes a breaking change: Amazon Keyspaces will no longer include Starfield C2 in its certificate chain. The application's trust store exclusively trusts Starfield Class 2, confirming connectivity will break.",
      "event_type": "AWS_CASSANDRA_PLANNED_LIFECYCLE_EVENT",
      "affected_service": "CASSANDRA",
      "affected_accounts": [
        {
          "account_id": "123456789012",
          "account_name": "prod-us-east",
          "environment_type": "production",
          "affected_resources": ["arn:aws:cassandra:us-east-1:123456789012:keyspace/mykeyspace"]
        }
      ],
      "environment_breakdown": {
        "production_count": 1,
        "non_production_count": 0
      },
      "impact_analysis": {
        "action_required": true,
        "risk_level": "high",
        "impact_status": "confirmed",
        "summary": "Production account 123456789012 has 1 affected Keyspaces resource. Impact is confirmed — the application's trust store contains only Starfield Class 2, which will be removed from the certificate chain.",
        "suggested_next_steps": [
          "Update your trust store to include Amazon Root CA 1",
          "Test connectivity to Keyspaces using the updated trust store in a non-production environment first",
          "Deploy the updated trust store to production before the certificate change date",
          "Verify successful connections after the trust store update"
        ]
      },
      "cost_projection": null
    }
  ],
  "total_count": 1,
  "breaking_change_count": 1,
  "cost_implication_count": 0,
  "security_related_count": 0,
  "sns_publish_status": "sent"
}
```

### Error Response Schema

```json
{
  "status": "error",
  "error": "Failed to parse health event payload: <details>"
}
```

### Empty Response Schema

```json
{
  "status": "success",
  "notifications": [],
  "total_count": 0,
  "breaking_change_count": 0,
  "cost_implication_count": 0,
  "security_related_count": 0,
  "sns_publish_status": "skipped"
}
```


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

Note: Properties 1–5 test the agent's classification behavior (LLM + system prompt). Properties 6–10 test the consolidation and filtering logic in isolation (deterministic, no LLM). Properties 11–12 test the Impact_Analyzer and Cost_Estimator in isolation. Properties 13–14 test output format. Properties 15–18 test the SNS_Notifier, get_account_context, environment type determination, and limit parameter. Property 19 tests the Impact_Analyzer's confirmed/unconfirmed impact status and suggested next steps (remediation or verification). For LLM-dependent properties, clearly unambiguous notification descriptions are used to ensure deterministic behavior.

### Property 1: Breaking changes are classified BREAKING_CHANGE

*For any* notification whose description clearly describes a breaking change (service deprecation, API removal, endpoint retirement, certificate change causing connection failure, or any change that will cause workloads to stop functioning), the agent shall assign a classification of BREAKING_CHANGE.

**Validates: Requirements 4.1, 4.2**

### Property 2: Cost implications are classified COST_IMPLICATION

*For any* notification whose description clearly describes a cost implication (extended support fees, pricing changes, resource cost increases) and does NOT describe a breaking change, the agent shall assign a classification of COST_IMPLICATION.

**Validates: Requirements 5.1, 5.2**

### Property 3: Security events are classified SECURITY_RELATED

*For any* notification whose description clearly describes a security concern (security vulnerability, compliance issue, security patch) and does NOT describe a breaking change or cost implication, the agent shall assign a classification of SECURITY_RELATED.

**Validates: Requirements 6.1, 6.2**

### Property 4: Classification is mutually exclusive with priority ordering

*For any* notification, the agent shall assign exactly one classification from {BREAKING_CHANGE, COST_IMPLICATION, SECURITY_RELATED}. If a notification's description contains both breaking change and cost implication indicators, the classification shall be BREAKING_CHANGE. If it contains both cost implication and security indicators (but not breaking change), the classification shall be COST_IMPLICATION.

**Validates: Requirements 8.1, 8.2**

### Property 5: Every classification includes a valid reason referencing notification attributes

*For any* classified notification in the agent's output, the reason field shall be a non-empty string of at least one sentence that references at least one attribute of the original notification (service name, event type, or specific details from the description).

**Validates: Requirements 7.1, 7.2, 7.3**

### Property 6: Only open or upcoming events pass the status filter

*For any* set of events with mixed status codes in the health event payload, the agent shall process only events with status "open" or "upcoming", and shall exclude all events with status "closed" or any other value.

**Validates: Requirements 2.3**

### Property 7: All affected accounts enriched and processed before classification

*For any* health event payload with multiple affected accounts, the agent shall call `get_account_context` for each affected account and include all accounts in the classification output before proceeding to classification.

**Validates: Requirements 2.4, 13.1**

### Property 8: Related notifications consolidated into single view

*For any* set of notifications where multiple notifications relate to the same health event (same event type code and service) across different accounts, the `consolidate_notifications` tool shall group them into a single Consolidated_View. The number of consolidated views shall be less than or equal to the number of input notifications.

**Validates: Requirements 3.1**

### Property 9: Consolidated views contain account detail, org summary, and environment breakdown

*For any* Consolidated_View produced by the `consolidate_notifications` tool, the view shall contain: (a) account-level detail for each affected account, (b) an organization-wide impact summary, and (c) a breakdown categorizing impact separately for production and non-production environments.

**Validates: Requirements 3.2, 3.3, 3.4**

### Property 10: Adding related notification updates existing view

*For any* existing set of consolidated views and a new notification that relates to an existing view, calling `consolidate_notifications` with the new notification added shall not increase the number of consolidated views. The existing view shall be updated to include the new notification's account details.

**Validates: Requirements 3.5**

### Property 11: Impact analysis covers all affected accounts with environment-based risk scoring

*For any* BREAKING_CHANGE notification with affected accounts spanning both production and non-production environments, the `analyze_impact` tool shall: (a) produce an impact summary listing each affected account with its resources and required action, and (b) assign higher risk to production environments than non-production environments.

**Validates: Requirements 9.1, 9.2, 9.3**

### Property 12: Cost projections per account aggregate to org total

*For any* COST_IMPLICATION notification with multiple affected accounts where cost projections are determinable, the `estimate_cost` tool shall produce a per-account cost projection for each account, and the organization-wide total shall equal the sum of all per-account projected costs.

**Validates: Requirements 10.1, 10.2**

### Property 13: Output contains all required fields

*For any* list of classified notifications in the agent's output, each entry shall contain all required fields: notification_id, classification, reason, event_type, affected_service, affected_accounts, and environment_breakdown — with no null or missing values. The output shall also include total_count, breaking_change_count, cost_implication_count, and security_related_count fields with correct tallies.

**Validates: Requirements 11.1, 11.2, 11.3**

### Property 14: Classification-specific analysis included in output

*For any* notification classified as BREAKING_CHANGE, the output shall include a non-null impact_analysis field with required actions. *For any* notification classified as COST_IMPLICATION, the output shall include a non-null cost_projection field.

**Validates: Requirements 11.4, 11.5**

### Property 15: SNS publish contains required fields

*For any* completed classification and impact analysis, the `publish_to_sns` tool shall publish a structured JSON payload to the configured SNS topic containing: notification identifier, event type, affected service, classification, classification reason, impact analysis, cost projections, and affected accounts.

**Validates: Requirements 12.1, 12.2, 12.6**

### Property 16: get_account_context returns required fields

*For any* valid AWS account ID, the `get_account_context` tool shall return a dict containing account_name, ou_path (OU membership path), tags (dictionary of account tags), and environment_type.

**Validates: Requirements 13.2, 13.6**

### Property 17: Environment type determined from account context

*For any* account context with tags or OU membership indicating a production environment, the `get_account_context` tool shall set environment_type to "production". *For any* account context indicating a non-production environment, it shall set environment_type to "non-production".

**Validates: Requirements 13.4**

### Property 18: Limit parameter caps notification count

*For any* health event payload containing N notifications and a limit parameter set to a positive integer L where L < N, the agent shall process at most L notifications. When limit is 0 or omitted, the agent shall process all N notifications.

**Validates: Requirements 2.6**

### Property 19: Impact analysis includes impact_status and suggested next steps

*For any* BREAKING_CHANGE notification processed by the `analyze_impact` tool, the impact analysis shall include an `impact_status` field with a value of either `"confirmed"` or `"unconfirmed"`, and a non-empty `suggested_next_steps` list where each step is a non-empty string specific to the notification's service and event type. When `impact_status` is `"confirmed"`, the steps shall be remediation-oriented (addressing the confirmed issue). When `impact_status` is `"unconfirmed"`, the steps shall be verification-oriented (determining the actual impact).

**Validates: Requirements 9.5, 9.6, 9.7, 9.8, 9.9**


## IAM Permissions

The AgentCore Runtime IAM role must include the following permissions for the agent's tools to function correctly:

**AWS Organizations** (for `get_account_context`):
- `organizations:DescribeAccount`
- `organizations:ListParents`
- `organizations:ListTagsForResource`
- `organizations:DescribeOrganizationalUnit`

**Amazon SNS** (for `publish_to_sns`):
- `sns:Publish` on the configured SNS topic ARN

**Amazon Bedrock** (for the Strands Agent LLM):
- `bedrock:InvokeModel`
- `bedrock:InvokeModelWithResponseStream`

**Amazon ECR** (for container image pull):
- `ecr:GetDownloadUrlForLayer`
- `ecr:BatchGetImage`
- `ecr:GetAuthorizationToken`

**Validates: Requirement 14**


## Error Handling

### Malformed Payload (Req 1.3, 2.5)
- If the agent receives a malformed or unparseable health event payload (missing required fields, invalid JSON, empty payload), it returns a structured error response with `"status": "error"` and a description of the validation failure.

### Status Filtering (Req 2.3)
- Events with status codes other than "open" or "upcoming" are silently filtered out. If all events are filtered, the agent returns the empty response schema.

### Account Context API Failure (Req 13.5)
- If the AWS Organizations API call fails for an account (permissions error, network issue, account not found), the `get_account_context` tool logs the failure and returns a fallback dict with `environment_type` set to `"unknown"`, `account_name` set to the account ID, `ou_path` set to `"unknown"`, and `tags` set to an empty dict. Processing continues with degraded context.

### Description Missing
- If the health event payload does not include a description, the agent classifies based on available fields (service, eventTypeCode, eventTypeCategory) and notes the missing description in the reason.

### Impact Analysis — No Affected Resources (Req 9.4)
- When the Impact_Analyzer finds no affected resources in any account, it returns `action_required: false` with a summary indicating no action is required.

### Cost Estimation — Unknown Projection (Req 10.4)
- When the Cost_Estimator cannot determine a cost projection (insufficient data, unsupported event type), it returns `projectable: false` with a reason explaining why the cost impact is unknown.

### SNS Topic ARN Not Configured (Req 12.4)
- If the `SNS_TOPIC_ARN` environment variable is not set, the `publish_to_sns` tool logs a warning and returns `{"status": "skipped", "reason": "SNS_TOPIC_ARN not configured"}`. The agent includes `"sns_publish_status": "skipped"` in the output. Processing is not interrupted.

### SNS Publish Failure (Req 12.5)
- If the SNS publish call fails (permissions error, topic not found, network issue), the `publish_to_sns` tool logs the failure and returns `{"status": "failed", "error": "<details>"}`. The agent includes the failure details in the output under `sns_publish_status` as `"failed"`.

### LLM Response Parsing
- If the LLM's streamed response doesn't contain valid JSON, the entrypoint attempts to extract JSON from the response text. If extraction fails, it returns a generic error response.

### Streaming Response
- The `@app.entrypoint` async generator filters Strands streaming events and yields only the final result text (the "result" event). Downstream consumers (e.g., the aha-eventbridge-lambda) receive a clean text response, not the full streaming trace with tool calls and intermediate reasoning. This keeps the response payload manageable and predictable.


## Testing Strategy

### Unit Tests

Unit tests cover specific examples, edge cases, and integration points:

**Account Context Tool:**
- Successful context retrieval: Mock boto3 Organizations client; verify account name, OU path, tags, and environment_type returned.
- API failure fallback: Mock Organizations API raising an exception; verify fallback dict with `environment_type: "unknown"`.
- Production account detection: Mock account with production tags; verify `environment_type: "production"`.
- Non-production account detection: Mock account with non-production OU; verify `environment_type: "non-production"`.
- OU path construction: Mock nested OU hierarchy; verify full OU path string.

**Consolidation Tool:**
- Single event, multiple accounts: Verify notifications for the same event across accounts are grouped into one view.
- Multiple distinct events: Verify distinct events produce separate consolidated views.
- Environment breakdown: Verify prod/non-prod categorization is correct given enriched account context.
- Update existing view: Verify adding a related notification updates the existing view rather than creating a new one.
- Org-wide summary: Verify the consolidated view includes an organization-wide impact summary.

**Impact Analyzer Tool:**
- Known BREAKING_CHANGE with prod accounts: Verify high risk score and action required.
- Known BREAKING_CHANGE with only non-prod accounts: Verify lower risk score.
- No affected resources: Verify `action_required: false` and "no action required" summary.
- Mixed environments: Verify prod gets higher risk than non-prod in the same analysis.
- Confirmed impact: Verify `impact_status: "confirmed"` when the agent has definitive information about resource impact, and `suggested_next_steps` is a non-empty list of remediation steps (e.g., "Update your trust store to include Amazon Root CA 1").
- Unconfirmed impact: Verify `impact_status: "unconfirmed"` when the agent cannot confirm actual impact, and `suggested_next_steps` is a non-empty list of verification steps (e.g., "Check if your applications use custom TLS trust stores").
- Next steps specificity: Verify that `suggested_next_steps` reference the affected service and notification type — confirmed steps describe remediation actions, unconfirmed steps describe verification actions. Neither should be generic "check your resources" steps.

**Cost Estimator Tool:**
- Known COST_IMPLICATION with determinable costs: Verify per-account projections and org total.
- Unknown cost projection: Verify `projectable: false` with reason.
- Historical tracking: Verify historical data is stored after processing.
- Multi-account aggregation: Verify org total equals sum of per-account costs.

**SNS Notifier Tool:**
- Successful publish: Mock boto3 SNS client; verify publish called with structured JSON payload containing all required fields.
- Missing SNS_TOPIC_ARN: Unset env var; verify warning logged and `{"status": "skipped"}` returned.
- Publish failure: Mock SNS publish raising an exception; verify failure logged and `{"status": "failed", "error": "..."}` returned.
- Payload structure: Verify SNS message contains notification_id, event_type, affected_service, classification, reason, impact_analysis, cost_projection, and affected_accounts.

**Agent Integration:**
- Known BREAKING_CHANGE example: Use the Keyspaces TLS certificate notification payload; verify BREAKING_CHANGE classification with impact analysis and SNS publish.
- Known COST_IMPLICATION example: Use the EKS extended support notification payload; verify COST_IMPLICATION classification with cost projection.
- Known SECURITY_RELATED example: Construct a security vulnerability notification payload; verify SECURITY_RELATED classification.
- Empty payload: Provide an empty health event; verify the agent returns the error response schema.
- Malformed payload: Provide invalid JSON; verify the agent returns an error response.
- Limit parameter: Provide a payload with multiple notifications and limit=1; verify only 1 notification processed.
- SNS_TOPIC_ARN not set: Unset env var; verify agent completes classification and returns `sns_publish_status: "skipped"`.

### Property-Based Tests

Property-based tests verify universal properties across randomly generated inputs. The project shall use **Hypothesis** (Python) as the property-based testing library.

Each property test must:
- Run a minimum of 100 iterations
- Reference the design property it validates via a comment tag

**Test implementations:**

1. **Feature: phd-notification-classifier, Property 1: Breaking changes are classified BREAKING_CHANGE**
   - Generator: random notification dicts with descriptions that unambiguously describe breaking changes (templates like "{service} will be deprecated and will no longer function after {date}", "Applications will fail to connect to {service} after {date}").
   - Assertion: agent classifies as BREAKING_CHANGE.

2. **Feature: phd-notification-classifier, Property 2: Cost implications are classified COST_IMPLICATION**
   - Generator: random notification dicts with descriptions that unambiguously describe cost implications (templates like "Extended support for {service} version {version} will incur additional charges after {date}") and explicitly no breaking change or security language.
   - Assertion: agent classifies as COST_IMPLICATION.

3. **Feature: phd-notification-classifier, Property 3: Security events are classified SECURITY_RELATED**
   - Generator: random notification dicts with descriptions that unambiguously describe security concerns (templates like "A security vulnerability has been identified in {service} requiring immediate patching") and explicitly no breaking change or cost language.
   - Assertion: agent classifies as SECURITY_RELATED.

4. **Feature: phd-notification-classifier, Property 4: Classification is mutually exclusive with priority ordering**
   - Generator: random notification dicts with descriptions containing both breaking change AND cost implication language.
   - Assertion: agent classifies as BREAKING_CHANGE (priority rule), and classification is exactly one of {BREAKING_CHANGE, COST_IMPLICATION, SECURITY_RELATED}.

5. **Feature: phd-notification-classifier, Property 5: Every classification includes a valid reason**
   - Generator: random notification dicts (any content — breaking, cost, security, or mixed).
   - Assertion: `reason` is non-empty, is at least one sentence (>10 chars), and references at least one of: the notification's service, event type, or a substring from the description.

6. **Feature: phd-notification-classifier, Property 6: Only open or upcoming events pass the status filter**
   - Generator: random lists of event dicts with status codes drawn from `["open", "upcoming", "closed", "unknown", "resolved"]`.
   - Assertion: after filtering, all processed events have status in `{"open", "upcoming"}`, and no events with other statuses are present. Tests filtering logic in isolation (no LLM).

7. **Feature: phd-notification-classifier, Property 7: All affected accounts enriched and processed**
   - Generator: random health event payloads with 1–10 affected account IDs.
   - Assertion: `get_account_context` is called for each affected account, and all accounts appear in the classification output. Tests with mocked Organizations API.

8. **Feature: phd-notification-classifier, Property 8: Related notifications consolidated into single view**
   - Generator: random sets of notifications where some share the same event type code and service across different accounts.
   - Assertion: number of consolidated views equals number of unique (eventTypeCode, service) pairs. Tests the consolidation tool in isolation.

9. **Feature: phd-notification-classifier, Property 9: Consolidated views contain required fields**
   - Generator: random sets of notifications with mixed production and non-production accounts (using enriched account context).
   - Assertion: every consolidated view contains account-level detail for each affected account, an org-wide impact summary, and a production/non-production breakdown. Tests the consolidation tool in isolation.

10. **Feature: phd-notification-classifier, Property 10: Adding related notification updates existing view**
    - Generator: random existing consolidated views plus a new notification that matches an existing view's event type.
    - Assertion: re-consolidating does not increase the number of views; the matching view is updated with the new account details. Tests the consolidation tool in isolation.

11. **Feature: phd-notification-classifier, Property 11: Impact analysis with environment-based risk scoring**
    - Generator: random BREAKING_CHANGE notifications with affected accounts spanning both production and non-production environments.
    - Assertion: impact summary lists all affected accounts with resources and actions; production accounts receive higher risk scores than non-production accounts. Tests the Impact_Analyzer in isolation.

12. **Feature: phd-notification-classifier, Property 12: Cost projections aggregate correctly**
    - Generator: random COST_IMPLICATION notifications with 1–10 affected accounts and determinable per-account costs.
    - Assertion: org-wide total equals sum of per-account projected costs. Tests the Cost_Estimator in isolation.

13. **Feature: phd-notification-classifier, Property 13: Output contains all required fields**
    - Generator: random lists of classified notification dicts (1–10 notifications).
    - Assertion: every entry contains keys `notification_id`, `classification`, `reason`, `event_type`, `affected_service`, `affected_accounts`, `environment_breakdown` — all non-null. Output also contains `total_count`, `breaking_change_count`, `cost_implication_count`, `security_related_count` with correct tallies.

14. **Feature: phd-notification-classifier, Property 14: Classification-specific analysis in output**
    - Generator: random classified notifications with BREAKING_CHANGE and COST_IMPLICATION types.
    - Assertion: BREAKING_CHANGE entries have non-null `impact_analysis`; COST_IMPLICATION entries have non-null `cost_projection`.

15. **Feature: phd-notification-classifier, Property 15: SNS publish contains required fields**
    - Generator: random completed classification results with various notification types.
    - Assertion: the SNS publish payload contains notification_id, event_type, affected_service, classification, reason, impact_analysis, cost_projection, and affected_accounts. Tests the SNS_Notifier in isolation with mocked boto3 SNS client.

16. **Feature: phd-notification-classifier, Property 16: get_account_context returns required fields**
    - Generator: random AWS account IDs with mocked Organizations API responses (random account names, OU paths, tag sets).
    - Assertion: returned dict contains `account_name`, `ou_path`, `tags`, and `environment_type` — all non-null. Tests the tool in isolation with mocked boto3.

17. **Feature: phd-notification-classifier, Property 17: Environment type determined from account context**
    - Generator: random account contexts with tags containing "Environment=Production" or OU paths containing "Production" (and corresponding non-production variants).
    - Assertion: accounts with production indicators have `environment_type: "production"`; accounts with non-production indicators have `environment_type: "non-production"`. Tests the environment type determination logic in isolation.

18. **Feature: phd-notification-classifier, Property 18: Limit parameter caps notification count**
    - Generator: random notification lists of size 1–20 and random limit values (0, 1, 5, 10, 50).
    - Assertion: when limit > 0 and limit < len(notifications), the output contains at most `limit` notifications. When limit is 0 or omitted, all notifications are processed.

19. **Feature: phd-notification-classifier, Property 19: Impact analysis includes impact_status and suggested next steps**
    - Generator: random BREAKING_CHANGE notifications with varying levels of available information — some where impact can be definitively confirmed (e.g., specific resource ARNs known to be affected) and some where impact cannot be confirmed (e.g., account uses the service but application-level configuration is unknown).
    - Assertion: every impact analysis contains `impact_status` with value `"confirmed"` or `"unconfirmed"`, and a non-empty `suggested_next_steps` list of non-empty strings. When `impact_status` is `"confirmed"`, steps are remediation-oriented. When `impact_status` is `"unconfirmed"`, steps are verification-oriented. Tests the Impact_Analyzer in isolation.

**Testing considerations:**

- Properties 1–5 require invoking the Strands Agent with the LLM. To make these tests practical:
  - Use clearly unambiguous notification descriptions so the LLM's classification is deterministic
  - Use template-based generators that produce descriptions with obvious classification signals
  - Consider using a mock/stub LLM for fast iteration during development, with periodic integration tests against the real model
- Properties 6–12, 15–19 test individual tool functions in isolation and do not require the LLM — these run at full speed with 100+ iterations
- Properties 13–14 test output structure and can be validated against the output schema
- For CI/CD, LLM-dependent property tests (1–5) can run with a smaller iteration count (e.g., 20) to manage cost and latency, while deterministic property tests (6–19) run the full 100+ iterations
