#!/usr/bin/env python3
"""CDK app entry point for Bedrock Quota Agent infrastructure."""
import os
import aws_cdk as cdk

from stacks.application_stack import ApplicationStack
from stacks.cache_stack import CacheStack
from stacks.slack_integration_stack import SlackIntegrationStack
from stacks.observability_stack import ObservabilityStack


app = cdk.App()

# Get environment: DEPLOY_ENV env var > CDK context > default 'dev'
env_name = os.environ.get("DEPLOY_ENV") or app.node.try_get_context("environment") or "dev"
aws_env = cdk.Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1"
)

# Create the cache stack first
cache_stack = CacheStack(
    app,
    f"BedrockQuotaAgent-{env_name}-Cache",
    env=aws_env,
    environment=env_name,
    description=f"Bedrock Quota Agent cache stack for {env_name} environment"
)

# Create the main application stack with cache parameters
application_stack = ApplicationStack(
    app,
    f"BedrockQuotaAgent-{env_name}-Application",
    env=aws_env,
    environment=env_name,
    cache_table_name=cache_stack.table.table_name,
    cache_table_arn=cache_stack.table.table_arn,
    description=f"Bedrock Quota Agent application stack for {env_name} environment"
)

# Slack integration stack — connects the AgentCore runtime to Slack via
# API Gateway + Lambda. Creates a Secrets Manager secret that must be
# populated with Slack credentials post-deploy (see README).
slack_stack = SlackIntegrationStack(
    app,
    f"BedrockQuotaAgent-{env_name}-SlackIntegration",
    env=aws_env,
    environment=env_name,
    runtime_arn=application_stack.runtime_arn,
    dedup_table_name=cache_stack.table.table_name,
    slack_secret_name=app.node.try_get_context("slack_secret_name"),
    slack_slash_command=os.environ.get("SLACK_SLASH_COMMAND", "/bedrock"),
    description=f"Bedrock Quota Agent Slack integration stack for {env_name} environment"
)
slack_stack.add_dependency(application_stack)
slack_stack.add_dependency(cache_stack)

# Optional: Deploy Observability stack for logging and tracing
# Uncomment to enable CloudWatch Logs delivery and X-Ray tracing
#
# X-Ray configuration from context (optional overrides)
xray_indexing_pct = app.node.try_get_context("xray_indexing_percentage")
xray_retain = app.node.try_get_context("xray_retain_on_delete")

observability_stack = ObservabilityStack(
    app,
    f"BedrockQuotaAgent-{env_name}-Observability",
    env=aws_env,
    environment=env_name,
    runtime_id=application_stack.runtime_id,  # Cross-stack reference
    runtime_arn=application_stack.runtime_arn,  # Cross-stack reference
    xray_indexing_percentage=int(xray_indexing_pct) if xray_indexing_pct is not None else 1,
    xray_retain_on_delete=xray_retain.lower() == "true" if xray_retain is not None else None,
    description=f"Bedrock Quota Agent observability stack for {env_name} environment"
)
observability_stack.add_dependency(application_stack)

app.synth()
