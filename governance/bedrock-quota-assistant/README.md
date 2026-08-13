# Bedrock Quota Assistant

An AI agent that helps customers understand their Amazon Bedrock usage, compare utilization against quotas, and create quota increase requests. Built with [Strands Agents SDK](https://github.com/strands-agents/strands-agents) and deployed on [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html).

> **This is sample code, for non-production usage.** You should work with your security and legal teams to meet your organizational security, regulatory and compliance requirements before deployment.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────────────┐
│  Slack Bot   │────▶│  API Gateway +   │────▶│  Bedrock AgentCore      │
│  (optional)  │     │  Lambda          │     │  Runtime (Container)    │
└──────────────┘     └──────────────────┘     │                         │
                                              │  ┌───────────────────┐  │
┌──────────────┐                              │  │  Strands Agent    │  │
│  CLI Client  │─────────────────────────────▶│  │  + Tools          │  │
└──────────────┘                              │  └───────────────────┘  │
                                              └───┬───┬───┬───┬────────┘
                                                  │   │   │   │
                               ┌──────────────────┘   │   │   └──────────────┐
                               ▼                      ▼   ▼                  ▼
                        ┌─────────────┐  ┌──────────────┐ ┌───────────┐ ┌───────────────┐
                        │  Service    │  │  CloudWatch  │ │ DynamoDB  │ │  AgentCore    │
                        │  Quotas API │  │  Metrics     │ │ (Cache)   │ │  Memory       │
                        └─────────────┘  └──────────────┘ └───────────┘ └───────────────┘

                        ┌─────────────────────────────────────────────┐
                        │  CloudWatch Logs + X-Ray Tracing (optional) │
                        └─────────────────────────────────────────────┘
```

## Features

- Check Bedrock model quotas (TPM/RPM limits) with fast DynamoDB-cached lookups (~3s)
- Query CloudWatch metrics for model invocation usage
- Discover recently used models, ranked by usage
- Per-inference-profile usage breakdown with shared quota warnings
- Analyze quota utilization vs limits with automatic risk flagging
- Draft and submit quota increase requests with case body, CLI command, and console links
- Multi-turn conversations with context memory via AgentCore Memory
- Optional Slack integration with streaming responses (Agents & AI Apps UI)

## Prerequisites

- Python 3.12+
- Node.js 20+ (for CDK CLI)
- Docker or [Finch](https://github.com/runfinch/finch) (for building the agent container)
- AWS CLI configured with appropriate credentials
- AWS CDK v2 (`npm install -g aws-cdk`)
- AWS Business, Enterprise, or Unified Operations Support plan (required for submitting quota increase cases via the agent)

## Quick Start

```bash
# One-time setup
make setup
source .venv/bin/activate

# Bootstrap CDK (first time per account/region — uses REGION variable)
make bootstrap

# Deploy all stacks (defaults: ENV=dev, REGION=us-east-1)
make deploy
```

To customise environment or region:

```bash
make bootstrap REGION=eu-west-1
make deploy ENV=prod REGION=eu-west-1
```

Bootstrap and deploy must target the same region.

If using Finch instead of Docker:

```bash
export CDK_DOCKER=finch
make deploy
```

Run `make help` to see all available targets.

## Deployment

### Stacks

Deployment creates four CloudFormation stacks:

| Stack | Purpose |
|-------|---------|
| `BedrockQuotaAgent-{env}-Cache` | DynamoDB table for quota code caching + Lambda refresh (every 7 days) |
| `BedrockQuotaAgent-{env}-Application` | AgentCore runtime, ECR repository, IAM role, Memory resource |
| `BedrockQuotaAgent-{env}-SlackIntegration` | API Gateway + Slack Bolt Lambda (optional) |
| `BedrockQuotaAgent-{env}-Observability` | CloudWatch Logs delivery + X-Ray tracing (optional) |

### Deploying Individual Stacks

```bash
make deploy-cache           # Cache only
make deploy-app             # Application (auto-deploys Cache dependency)
make deploy-slack           # Slack integration (auto-deploys Cache + Application)
make deploy-observability   # Observability (auto-deploys Application)
```

CDK automatically deploys stack dependencies — deploying Application will deploy Cache first if needed.

### Post-deploy: Slack Credentials

If using the Slack integration, populate the credentials secret:

```bash
make set-slack-creds                          # or: make set-slack-creds ENV=prod REGION=eu-west-1
```

This prompts for the bot token and signing secret without echoing them, passes
them to AWS Secrets Manager through a `0600` temporary file, and deletes that file
on exit.

Do not pass credentials on the command line. A command such as
`--secret-string '{"SLACK_BOT_TOKEN":"xoxb-..."}'` writes the token to your shell
history file and exposes it in the process table for the lifetime of the command.
Shell history is frequently synced to dotfiles repositories.

If you cannot use `make`, use a file reference rather than an inline argument:

```bash
umask 077 && cat > slack-creds.json <<'JSON'
{"SLACK_BOT_TOKEN":"xoxb-...","SLACK_SIGNING_SECRET":"..."}
JSON
aws secretsmanager put-secret-value \
    --secret-id "bedrock-quota-agent/dev/slack-credentials" \
    --secret-string file://slack-creds.json
rm slack-creds.json
```

### Slack App Setup

1. Go to https://api.slack.com/apps and create a new app
2. Go to OAuth & Permissions, add scopes: `app_mentions:read`, `assistant:write`, `channels:history`, `chat:write`, `commands`, `groups:history`, `im:history`, `im:read`, `im:write`, `mpim:history`
3. Install the app to your workspace — copy the Bot User OAuth Token (`xoxb-...`)
4. Go to Basic Information → App Credentials — copy the Signing Secret
5. Populate the secret with `make set-slack-creds` (see above)
6. Go to Event Subscriptions, enable events, set the Request URL to the `SlackEventsUrl` stack output
7. Subscribe to bot events: `app_mention`, `message.channels`, `message.im`
8. (Optional) Create a slash command (default: `/bedrock`) using the same `SlackEventsUrl`

### Stack Outputs

After deployment, note these outputs from the Application stack:
- `RuntimeArn` — ARN for invoking the agent
- `MemoryId` — AgentCore Memory resource ID
- `RepositoryUri` — ECR repository for the agent image

### CDK Context Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `environment` | `dev` | Deployment environment (`dev`, `staging`, `prod`) |
| `region` | `us-east-1` | AWS region |
| `xray_indexing_percentage` | `1` | X-Ray span indexing percentage (0–100) |
| `xray_retain_on_delete` | Per-environment | Keep Transaction Search enabled on stack delete |

## Usage

### Example Queries

```
"What are the quotas for claude haiku?"
"Check utilization for nova pro over the last 4 hours"
"Which model have I used the most in the past 24 hours?"
"I need a quota increase for claude sonnet 4 in eu-west-1"
```

### CLI Client

```bash
export AGENTCORE_ARN="arn:aws:bedrock-agentcore:REGION:ACCOUNT_ID:runtime/RUNTIME_ID"
python3 scripts/quota_cli.py
```

Commands: `/new`, `/actor`, `/session`, `/help`, `/quit`

### Agent Tools

| Tool | Purpose |
|------|---------|
| `get_customer_profile` | Overview of all models, quotas, and inference profiles in the account |
| `get_bedrock_model_quotas` | Check quotas (TPM/RPM limits) |
| `get_bedrock_model_invocation_metrics` | CloudWatch usage metrics |
| `list_available_bedrock_models` | List models available in a region |
| `list_active_bedrock_models` | Discover recently used models |
| `list_active_inference_profiles` | Per-profile usage breakdown |
| `check_quota_utilization` | Usage vs limits with automatic % calculation |
| `draft_quota_increase_request` | Generate ready-to-file quota increase request |
| `submit_quota_increase_case` | Submit drafted request as AWS Support case |

## Project Structure

```
bedrock-quota-assistant/
├── src/                           # Agent source code
│   ├── agent.py                   # AgentCore entrypoint (Strands Agent wiring)
│   ├── config.py                  # Centralized configuration (SSM, env vars)
│   ├── models.py                  # Model catalog and friendly-name resolver
│   ├── tools/                     # One @tool function per file
│   ├── helpers/                   # Shared logic (used by 2+ tools)
│   ├── prompts/
│   │   └── system_prompt.md
│   ├── Dockerfile                 # ARM64 container image
│   └── requirements.txt
├── infra/                         # CDK infrastructure (Python)
│   ├── app.py                     # CDK app entrypoint
│   ├── cdk.json
│   ├── requirements.txt
│   ├── stacks/                    # CDK stacks
│   ├── custom_constructs/         # Reusable L3 constructs per stack
│   └── lambda/                    # Supporting Lambda functions
│       ├── cache_refresh/         # Scheduled quota code cache refresh
│       └── slack_integration/     # Slack Bolt handler
├── tests/                         # All tests
│   ├── unit/                      # Unit tests (tools, helpers, infra)
│   ├── property/                  # Property-based tests (Hypothesis)
│   └── integration/               # End-to-end tests
├── scripts/
│   └── quota_cli.py               # Interactive CLI client
├── Makefile                       # All build/deploy/test targets
├── MUTATING_ACTIONS.md            # Write-action safety documentation
├── pytest.ini
└── requirements-dev.txt
```

## Development

```bash
make test          # Run all tests
make lint          # Run ruff linter
make docker-build  # Build agent container locally
make synth         # CDK synthesize (dry-run)
make clean         # Remove build artifacts
```

### Running Specific Tests

```bash
pytest tests/unit/ -v                  # Unit tests only
pytest tests/property/ -v              # Property-based tests only (Hypothesis)
pytest tests/unit/tools/ -v            # Just the tool tests
pytest tests/unit/infra/ -v            # Just the CDK infrastructure tests
pytest -m "not integration" -v         # Skip integration tests
pytest --cov=src tests/                # With coverage report
```

## Cleanup

```bash
make destroy
# Or with specific environment:
make destroy ENV=prod REGION=eu-west-1
```

**Warning**: This permanently deletes all resources including the AgentCore runtime, ECR images, conversation history, and cached data.

## Security

See [MUTATING_ACTIONS.md](MUTATING_ACTIONS.md) for details on write-action permissions. The agent can submit AWS Support cases (the only mutating action) — this is enabled by default in the IAM role but gated by explicit user confirmation in the agent workflow.

## License

MIT — see [LICENSE](LICENSE) for details.
