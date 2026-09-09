#!/usr/bin/env node
// Copyright (c) 2026 Amazon Web Services
// Licensed under the MIT License
// See LICENSE file in the project root for full license information.

import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { DartAgentStack } from "../lib/DartAgentStack";

const app = new cdk.App();

const env = app.node.tryGetContext("env") || process.env.ENVIRONMENT || "dev";
const account =
  process.env.CDK_DEFAULT_ACCOUNT ||
  app.node.tryGetContext("account") ||
  "123456789012";
const region =
  process.env.CDK_DEFAULT_REGION ||
  app.node.tryGetContext("region") ||
  "us-east-1";

new DartAgentStack(app, `DartAgent-${env}`, {
  env: { account, region },
  environment: env,
  description:
    "DART Agent — Dataset Audit & Readiness for Training. " +
    "Pre-flight validation for LLM fine-tuning datasets.",
  tags: {
    project: "dart-agent",
    environment: env,
    owner: "your-team",
    "data-classification": "internal",
  },
});

app.synth();
