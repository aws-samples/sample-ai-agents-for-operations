.PHONY: help setup bootstrap deploy deploy-cache deploy-app deploy-slack deploy-observability \
       synth destroy test lint docker-build clean set-slack-creds

# Pinned because set-slack-creds uses `read -s` to keep secrets off the terminal.
# `read -s` is a bash/zsh extension, not POSIX, and /bin/sh is dash on many
# Linux distributions.
SHELL := /bin/bash

# Configuration (override at command line: make deploy ENV=prod REGION=eu-west-1)
ENV    ?= dev
REGION ?= us-east-1
CDK    := npx cdk

# Stack name prefix
STACK_PREFIX := BedrockQuotaAgent-$(ENV)

##@ Setup

setup: ## Install all dependencies (venv, CDK, agent, dev tools)
	python3 -m venv .venv                                               # Create virtual environment
	. .venv/bin/activate && pip install -r requirements-dev.txt          # Dev tools (pytest, ruff, hypothesis)
	. .venv/bin/activate && cd infra && pip install -r requirements.txt  # CDK dependencies (aws-cdk-lib)
	. .venv/bin/activate && cd src && pip install -r requirements.txt    # Agent runtime dependencies (strands, bedrock-agentcore)
	@echo "\n✅ Setup complete. Run: source .venv/bin/activate"

bootstrap: ## Bootstrap CDK (first time only, per account/region)
	@echo "Bootstrapping CDK in:"
	@echo "  Account: $$(aws sts get-caller-identity --query Account --output text)"
	@echo "  Region:  $(REGION)"
	cd infra && $(CDK) bootstrap aws://$$(aws sts get-caller-identity --query Account --output text)/$(REGION)

##@ Deployment

deploy: ## Deploy all stacks
	cd infra && $(CDK) deploy --all -c environment=$(ENV) -c region=$(REGION) --require-approval never

deploy-cache: ## Deploy only the Cache stack (DynamoDB + refresh Lambda)
	cd infra && $(CDK) deploy $(STACK_PREFIX)-Cache -c environment=$(ENV) -c region=$(REGION) --require-approval never

deploy-app: ## Deploy only the Application stack (AgentCore runtime, ECR, IAM, Memory)
	cd infra && $(CDK) deploy $(STACK_PREFIX)-Application -c environment=$(ENV) -c region=$(REGION) --require-approval never

deploy-slack: ## Deploy only the Slack integration stack (API Gateway + Lambda)
	cd infra && $(CDK) deploy $(STACK_PREFIX)-SlackIntegration -c environment=$(ENV) -c region=$(REGION) --require-approval never

deploy-observability: ## Deploy only the Observability stack (CloudWatch Logs + X-Ray)
	cd infra && $(CDK) deploy $(STACK_PREFIX)-Observability -c environment=$(ENV) -c region=$(REGION) --require-approval never

set-slack-creds: ## Populate the Slack credentials secret (prompts; nothing is echoed or stored)
	@umask 077; \
	tmp=$$(mktemp); \
	trap 'rm -f "$$tmp"' EXIT INT TERM; \
	printf 'Slack bot token (xoxb-...): '; read -rs token; printf '\n'; \
	printf 'Slack signing secret: ';      read -rs signing; printf '\n'; \
	if [ -z "$$token" ] || [ -z "$$signing" ]; then \
	    echo "Both values are required; nothing was written." >&2; exit 1; \
	fi; \
	printf '{"SLACK_BOT_TOKEN":"%s","SLACK_SIGNING_SECRET":"%s"}' "$$token" "$$signing" > "$$tmp"; \
	aws secretsmanager put-secret-value \
	    --secret-id "bedrock-quota-agent/$(ENV)/slack-credentials" \
	    --secret-string "file://$$tmp" \
	    --region $(REGION) \
	    --no-cli-pager \
	    --query VersionId --output text > /dev/null; \
	echo "✅ Slack credentials stored for ENV=$(ENV) REGION=$(REGION)"

synth: ## Synthesize CDK templates (dry-run)
	cd infra && $(CDK) synth -c environment=$(ENV) -c region=$(REGION)

destroy: ## Destroy all stacks (requires confirmation)
	cd infra && $(CDK) destroy --all -c environment=$(ENV) -c region=$(REGION)

##@ Development

test: ## Run all tests
	cd tests && python -m pytest -v

lint: ## Run linter
	ruff check src/ tests/ infra/lambda/

docker-build: ## Build Docker image locally
	cd src && docker build -t bedrock-quota-agent:local .

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf infra/cdk.out

##@ Help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m [ENV=dev] [REGION=us-east-1]\n"} \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } \
		/^[a-zA-Z_-]+:.*?## / { printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
