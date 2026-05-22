# Sample AI Agents for Operations

A collection of sample AI agents that demonstrate how to automate IT operations tasks using AWS services. These agents help operations teams reduce manual toil, accelerate incident response, and enforce best practices through intelligent automation.

Each agent is a self-contained, deployable solution that follows a common pattern:
- **Receive** operational signals (health events, cost anomalies, security findings)
- **Reason** about the signal using LLMs (Amazon Bedrock)
- **Act** with appropriate guardrails (human-in-the-loop for high-risk actions)

## Agents by Domain

| Domain | Agent | Description | Status |
|---|---|---|---|
| **Health** | [AWS Health Notification Agent](health/aws-health-notification-agent/) | Classifies AWS Health Dashboard notifications, performs cross-account impact analysis, creates Jira tickets, and optionally executes approved remediation | Available |
| **Cost Optimization** | _Coming soon_ | Identifies misconfigured architectures and underutilized resources, suggests optimization plans | Planned |
| **Resiliency** | _Coming soon_ | Monitors resilience posture, recommends failover actions, validates recovery procedures | Planned |
| **Security** | _Coming soon_ | Automated security posture assessment, compliance drift detection, remediation guidance | Planned |
| **Cloud Governance** | _Coming soon_ | Enforces tagging standards, resource configuration policies, and end-of-life upgrades | Planned |

## Repository Structure

```
sample-ai-agents-for-operations/
├── health/                          # Health & availability domain
│   └── aws-health-notification-agent/   # PHD event classification + remediation
├── cost-optimization/               # Cost & efficiency domain (planned)
├── resiliency/                      # Resilience & recovery domain (planned)
├── security/                        # Security posture domain (planned)
├── governance/                      # Cloud governance domain (planned)
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
└── LICENSE
```

## Design Principles

These agents are built around a shared set of principles:

1. **Trust Through Incremental Validation** — Agents begin in observe-and-report mode. Autonomous actions are earned through demonstrated reliability, not granted by default.

2. **Human-in-the-Loop for Mutations** — Any action that modifies infrastructure requires explicit human approval via cryptographic tokens, two-step confirmation, or identity-aware authorization. Read-only operations are autonomous.

3. **Least-Privilege by Default** — IAM roles grant only read permissions out of the box. You must explicitly opt in to enable mutating capabilities after reviewing documented risks.

4. **Solve Real Problems** — Each agent addresses a specific operational challenge encountered in real-world environments, not hypothetical scenarios.

5. **Build for Self-Sufficiency** — Agents are fully deployable with a single command. You own and evolve your solution without dependency on external teams.

## Agent Autonomy Levels

Each agent documents its autonomy level and the conditions for progression:

| Level | Description | Example |
|---|---|---|
| **L0 — Observe** | Report findings only, no actions | Classify health events → publish to SNS |
| **L1 — Recommend** | Suggest actions, human decides | "Cluster X needs upgrade by July 23" → Jira ticket |
| **L2 — Confirm** | Propose action, human approves | "Upgrade cluster X?" → approval email → execute |
| **L3 — Autonomous** | Act within boundaries, report after | Auto-tag untagged resources (low-risk, reversible) |

Progression from L0 → L3 requires validated trust metrics: action success rate, false-positive rate, and blast radius assessment.

## Technology Stack

All agents in this repository use:

| Component | Technology | Purpose |
|---|---|---|
| Agent Framework | [Strands Agents](https://github.com/strands-agents/sdk-python) | Tool-using agent orchestration |
| LLM | Amazon Bedrock (Claude Sonnet) | Reasoning and classification |
| Runtime | Amazon Bedrock AgentCore Runtime | Managed agent execution environment |
| Infrastructure | AWS CloudFormation (SAM transform) | Declarative deployment |
| Deployment | Single `deploy.sh` script | One-command deploy, no additional tooling |

Individual agents may add domain-specific integrations (Jira, Slack, MCP servers, etc.) documented in their own README.

## Getting Started

Each agent has its own README with deployment instructions. The general pattern is:

```bash
cd <domain>/<agent-name>/
./deploy.sh
```

Start with the [AWS Health Notification Agent](health/aws-health-notification-agent/) — it demonstrates the full pattern including classification, impact analysis, multi-channel notifications, and human-approved remediation.

## Customise with Kiro IDE

These sample agents are designed to be customised using [Kiro IDE](https://kiro.dev)'s **spec-driven development** workflow. Each agent includes a `.kiro/specs/` folder containing structured specification files that Kiro uses to understand the agent's architecture and guide your modifications.

```
<agent>/.kiro/specs/
├── <feature-1>/
│   ├── requirements.md    # What the feature does and acceptance criteria
│   ├── design.md          # How it's implemented (architecture, data flow, APIs)
│   └── tasks.md           # Implementation tasks with completion status
├── <feature-2>/
│   ├── requirements.md
│   ├── design.md
│   └── tasks.md
└── ...
```

### How to customise an agent

1. **Open the agent folder in Kiro IDE** — Kiro automatically discovers the `.kiro/specs/` folder and understands the agent's architecture
2. **Tell Kiro what you want in natural language** — e.g., "I want to send notifications to PagerDuty instead of Slack" or "Add a new classification category for compliance events"
3. **Kiro generates the spec for you** — it creates the requirements, design, and task breakdown automatically, following the patterns established by existing specs
4. **Review and refine** — adjust the generated spec if needed, then let Kiro implement the code changes guided by the spec

You don't need to write specs manually — Kiro builds them from your natural language description using the existing specs as architectural context.

### Example customisations

| What you want to do | Tell Kiro |
|---|---|
| Add a new notification channel | "Send notifications to PagerDuty instead of Slack" |
| Change classification rules | "Add a COMPLIANCE category for regulatory events" |
| Add a new remediation action | "Support RDS engine upgrades with the same approval flow as EKS" |
| Integrate with a different ticketing system | "Replace Jira with ServiceNow for ticket creation" |
| Add team routing by new criteria | "Route tickets by AWS resource tag 'Team' in addition to OU" |

Kiro uses the existing specs as architectural context — it understands how the current integrations work and generates changes that fit the established patterns.

## Contributing

We welcome contributions — bug fixes, documentation improvements, and new agents. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

If you build an agent that solves an operational challenge and want to share it, open a pull request with:

1. The agent in the appropriate domain folder
2. A README with deployment instructions
3. A threat model for any infrastructure-modifying actions
4. Unit tests runnable without AWS credentials

### Agent Design Standards

Every agent in this repository follows these standards:

- Deploy with a single command (`deploy.sh`)
- Work in the deployer's own AWS account (no cross-account assumptions)
- Default to read-only IAM permissions (mutations require explicit opt-in)
- Include human-in-the-loop for any infrastructure-modifying actions
- Document failure modes and blast radius for mutating actions
- Follow the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/)

## Security

All agents follow a defense-in-depth security model:

- **IAM least-privilege** — Scoped to specific resource ARNs, read-only by default
- **Encryption** — Data at rest (KMS) and in transit (TLS 1.2+)
- **Human approval gates** — Cryptographic tokens for mutating actions
- **Audit trails** — CloudTrail + CloudWatch for all API calls
- **Prompt injection defense** — Structured system prompts with input/instruction separation

For security concerns, see [CONTRIBUTING.md](CONTRIBUTING.md#security-issue-notifications).

## Important Notes

- **This is sample code, not a production service.** These agents demonstrate how to use AWS services for operational automation. They are provided as starting points for your own implementations, not as maintained software products.
- **No warranty.** This code is provided as-is. Review, test, and adapt it for your environment before production use.
- **Your responsibility.** Once deployed in your account, you are responsible for the security, cost, and operational management of these resources per the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/).
- **Not published to package indexes.** These samples are not available via pip, npm, or Maven. Clone this repository to use them.
- **Feedback welcome.** File issues or pull requests on GitHub. We review contributions but cannot guarantee response times.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
