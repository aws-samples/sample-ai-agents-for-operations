#!/usr/bin/env python3
"""MIO Agent CDK App entry point."""

import aws_cdk as cdk

from stacks.mio_agent_stack import MIOAgentStack

app = cdk.App()

MIOAgentStack(
    app,
    "MIOAgentStack",
    description="MIO Agent — Monitoring Intelligence and Observability Agent",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region") or "us-east-1",
    ),
)

app.synth()
