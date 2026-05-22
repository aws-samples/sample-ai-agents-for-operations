# Roadmap: MCP Tool Integration for Distributed Impact Analysis

The current agent classifies health events using notification metadata and account context from AWS Organizations. The next phase extends the agent's analysis capabilities by integrating MCP (Model Context Protocol) tools contributed by different teams across the organization.

## Vision

Each team owns and maintains an MCP tool server that exposes their application and platform knowledge to the agent. When a health event arrives, the agent calls the relevant MCP tools to gather real impact data from the actual systems affected — not just metadata from the notification.

```
                          ┌─────────────────────────────┐
                          │  phd-notification-classifier │
                          │       (Strands Agent)        │
                          └──────────┬──────────────────┘
                                     │ MCP tool calls
                    ┌────────────────┼────────────────────┐
                    │                │                     │
              ┌─────▼─────┐   ┌─────▼──────┐   ┌─────────▼────────┐
              │ EKS Team  │   │ RDS Team   │   │ Platform Team    │
              │ MCP Server│   │ MCP Server │   │ MCP Server       │
              └─────┬─────┘   └─────┬──────┘   └─────────┬────────┘
                    │               │                     │
              ┌─────▼─────┐   ┌─────▼──────┐   ┌─────────▼────────┐
              │ EKS       │   │ RDS        │   │ Terraform State  │
              │ Clusters  │   │ Instances  │   │ CMDB / ServiceNow│
              └───────────┘   └────────────┘   └──────────────────┘
```

## How it works

- The agent discovers available MCP tools at runtime via AgentCore's MCP integration
- When a health event affects EKS, the agent calls the EKS team's MCP tool to check cluster versions, workload dependencies, and upgrade readiness
- When a health event affects RDS, the agent calls the RDS team's MCP tool to check engine versions, replica configurations, and maintenance windows
- The platform team's MCP tool provides infrastructure context: Terraform state, CMDB records, service ownership, and change management status
- Each team only needs to implement their MCP server — they don't need to understand the agent's classification logic

## Example MCP tools teams could build

| Team | MCP Tool | What it provides |
|---|---|---|
| EKS Platform | `get_cluster_details` | Cluster versions, node groups, addon versions, workload inventory |
| RDS/Database | `get_database_details` | Engine versions, parameter groups, replica topology, backup status |
| Networking | `get_vpc_dependencies` | VPC peering, Transit Gateway attachments, DNS configurations |
| Security | `get_compliance_status` | Security Hub findings, GuardDuty alerts, patch compliance |
| Platform/Infra | `get_terraform_state` | Resource state, drift detection, module versions |
| ServiceNow/CMDB | `get_service_ownership` | Service owners, escalation paths, change windows |
| Cost/FinOps | `get_cost_breakdown` | Per-resource cost data, savings plans, reserved instances |

## Real-world example: Keyspaces TLS certificate change

Consider the Keyspaces Starfield C2 certificate chain removal notification. The agent classifies it as BREAKING_CHANGE because applications that exclusively trust Starfield C2 will fail to connect. But the agent can't determine the actual impact without knowing which applications use custom trust stores and what certificates they trust.

With MCP tools, each application team builds their own MCP server exposing a `get_truststore_details` tool. When this health event arrives:

1. The agent classifies the notification as BREAKING_CHANGE
2. The agent calls each application team's `get_truststore_details` tool for the affected accounts
3. Team A's MCP server reports: "Our Java app uses a custom trust store pinned to Starfield C2 — we are affected"
4. Team B's MCP server reports: "Our app uses the default JVM trust store with Amazon Root CA 1 — we are not affected"
5. The agent produces a precise impact summary: only Team A needs to take action

## Targeted notifications and automated ticket assignment by OU / service team

Today the agent publishes a single consolidated summary to one SNS topic. In the future, notifications and ticket creation should be OU-aware and service-aware — routing impact summaries to the specific teams that own the affected accounts and services.

```
Health Event
    │
    ▼
┌──────────────────────┐
│ Classify + Analyze   │
│ (with MCP tools)     │
└──────────┬───────────┘
           │
    ┌──────▼──────┐
    │ Route by OU │
    │ / Service   │
    └──┬───┬───┬──┘
       │   │   │
  ┌────▼┐ ┌▼───▼────┐ ┌──────────┐
  │Pay- │ │Platform │ │Orders    │
  │ments│ │Team     │ │Team      │
  │     │ │         │ │          │
  │Slack│ │Teams    │ │Slack     │
  │Jira │ │Jira     │ │(no Jira, │
  │     │ │         │ │no impact)│
  └─────┘ └─────────┘ └──────────┘
```

## Cost and Usage Report integration for real cost impact analysis

Today the cost estimator uses static per-service cost tables to project financial impact. In the future, the agent should integrate with AWS Cost and Usage Report (CUR) data to analyse the actual cost of impacted resources based on real billing data.

## Multi-environment remediation with Amazon Bedrock AgentCore Gateway + Policy Engine

As the system scales to manage remediation across multiple environments (dev, staging, production), a layered security architecture ensures the agent can't accidentally upgrade the wrong environment.

**Three-layer defense for production:**

1. **IAM**: Per-environment roles scoped to their own account's clusters. Prod role is read-only (no `eks:UpdateClusterVersion`)
2. **AgentCore Policy (Cedar)**: Deterministic rules enforced at the Gateway before tool calls reach the MCP server
3. **Human approval**: Two-step confirmation required before any write operation

## Identity-aware approval with Amazon Bedrock AgentCore Workload Identity

The current approval flow uses token-based authorization — anyone with the approval link can execute the remediation. In a multi-team environment, approvals should be identity-aware: only users authorized for a specific environment can approve upgrades in that environment.

## What teams need to do

1. Build an MCP server that exposes tools relevant to their domain
2. Register the MCP server with AgentCore
3. The agent automatically discovers and uses the new tools when relevant health events arrive

This is not yet implemented. Contributions and design proposals welcome.
