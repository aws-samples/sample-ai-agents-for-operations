# Implementation Plan: PHD Notification Classifier

## Overview

Implement a Python AI agent using Strands Agents SDK deployed on Amazon Bedrock AgentCore Runtime. The agent receives AWS Health event payloads from the aha-eventbridge-lambda Lambda function via `@app.entrypoint`, enriches events with account context from AWS Organizations, consolidates related notifications across accounts, classifies them into BREAKING_CHANGE / COST_IMPLICATION / SECURITY_RELATED categories using an LLM, performs impact analysis and cost estimation, and publishes structured summaries to a configured SNS topic. Each major capability is a separate `@tool` function orchestrated by the LLM agent.

## Tasks

- [x] 1. Project setup and data models
  - [x] 1.1 Update dependencies and create project structure
    - Update `requirements.txt` to include: `strands-agents`, `bedrock-agentcore`, `boto3`, `hypothesis` (test), `pytest` (test)
    - Ensure `phd_notification_classifier/tools/` directory exists with `__init__.py`
    - Ensure `phd_notification_classifier/tests/` directory exists with `__init__.py`
    - _Requirements: 1.1_

  - [x] 1.2 Create data models in `phd_notification_classifier/models.py`
    - Define `ClassificationResult` TypedDict/dataclass with fields: notification_id, classification, reason, event_type, affected_service, affected_accounts, environment_breakdown, impact_analysis, cost_projection
    - Define `ConsolidatedView` TypedDict/dataclass with fields: event_key, event_arns, service, eventTypeCode, eventDescription, affected_accounts, environment_breakdown, org_impact_summary
    - Define `ImpactAnalysis` TypedDict/dataclass with fields: notification_id, action_required, risk_level, affected_accounts, summary
    - Define `CostProjection` TypedDict/dataclass with fields: notification_id, projectable, per_account_costs, org_total_projected_cost, currency, reason, historical_reference
    - Define `AccountContext` TypedDict/dataclass with fields: account_id, account_name, ou_path, tags, environment_type
    - Define `SNSPayload` TypedDict/dataclass with fields: notification_id, event_type, affected_service, classification, reason, affected_accounts, impact_analysis, cost_projection
    - _Requirements: 11.1, 11.2, 11.3, 12.2, 12.6, 13.6_

- [x] 2. Account Context Tool
  - [x] 2.1 Implement `phd_notification_classifier/tools/account_context.py`
    - Implement `get_account_context(account_id: str) -> dict` decorated with `@tool` from `strands`
    - Create a boto3 Organizations client
    - Call `describe_account` to get the account name
    - Call `list_parents` recursively to build the full OU membership path
    - Call `list_tags_for_resource` to retrieve account tags
    - Determine `environment_type` from account tags (e.g., "Environment" tag) or OU membership (e.g., OU path containing "Production")
    - Return dict with `account_id`, `account_name`, `ou_path`, `tags`, `environment_type`
    - On AWS Organizations API failure, log the error and return fallback dict with `environment_type: "unknown"`, `account_name` set to account_id, `ou_path: "unknown"`, `tags: {}`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [x]* 2.2 Write unit tests for get_account_context tool
    - Create `phd_notification_classifier/tests/test_account_context.py`
    - Test successful context retrieval: mock boto3 Organizations client; verify account_name, ou_path, tags, and environment_type returned
    - Test API failure fallback: mock Organizations API raising exception; verify fallback dict with `environment_type: "unknown"`
    - Test production account detection: mock account with production tags; verify `environment_type: "production"`
    - Test non-production account detection: mock account with non-production OU; verify `environment_type: "non-production"`
    - Test OU path construction: mock nested OU hierarchy; verify full OU path string
    - _Requirements: 13.2, 13.4, 13.5, 13.6_

  - [x]* 2.3 Write property test: get_account_context returns required fields
    - **Property 16: get_account_context returns required fields**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random AWS account IDs with mocked Organizations API responses (random account names, OU paths, tag sets)
    - Assert returned dict contains `account_name`, `ou_path`, `tags`, and `environment_type` — all non-null
    - **Validates: Requirements 13.2, 13.6**

  - [x]* 2.4 Write property test: Environment type determined from account context
    - **Property 17: Environment type determined from account context**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random account contexts with tags containing "Environment=Production" or OU paths containing "Production" (and corresponding non-production variants)
    - Assert accounts with production indicators have `environment_type: "production"`; accounts with non-production indicators have `environment_type: "non-production"`
    - **Validates: Requirements 13.4**

- [x] 3. Notification Consolidation Tool
  - [x] 3.1 Implement `phd_notification_classifier/tools/consolidation.py`
    - Implement `consolidate_notifications(notifications: list) -> list` decorated with `@tool`
    - Group notifications by same health event (matching event ARN or event type code + service)
    - Produce a `ConsolidatedView` per unique event with account-level detail
    - Categorize affected accounts as production or non-production based on enriched account context
    - Include environment breakdown (production_count, non_production_count)
    - Include organization-wide impact summary
    - Update existing consolidated views when new related notifications arrive (no duplicates)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x]* 3.2 Write unit tests for consolidation tool
    - Create/update `phd_notification_classifier/tests/test_consolidation.py`
    - Test single event across multiple accounts grouped into one view
    - Test multiple distinct events produce separate views
    - Test environment breakdown (prod/non-prod categorization)
    - Test update existing view with new related notification
    - Test org-wide summary included in each view
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x]* 3.3 Write property test: Related notifications consolidated into single view
    - **Property 8: Related notifications consolidated into single view**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random sets of notifications where some share the same event type code and service across different accounts
    - Assert number of consolidated views equals number of unique (eventTypeCode, service) pairs
    - **Validates: Requirements 3.1**

  - [x]* 3.4 Write property test: Consolidated views contain required fields
    - **Property 9: Consolidated views contain account detail, org summary, and environment breakdown**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random sets of notifications with mixed production and non-production accounts
    - Assert every consolidated view contains account-level detail, org-wide impact summary, and prod/non-prod breakdown
    - **Validates: Requirements 3.2, 3.3, 3.4**

  - [x]* 3.5 Write property test: Adding related notification updates existing view
    - **Property 10: Adding related notification updates existing view**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random existing consolidated views plus a new notification matching an existing view's event type
    - Assert re-consolidating does not increase the number of views; matching view is updated
    - **Validates: Requirements 3.5**

- [x] 4. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Impact Analyzer Tool
  - [x] 5.1 Implement `phd_notification_classifier/tools/impact_analyzer.py`
    - Implement `analyze_impact(notification: dict, affected_accounts: list) -> dict` decorated with `@tool`
    - Inspect all affected accounts and resources for a BREAKING_CHANGE notification
    - Assign higher risk scores to production environments than non-production
    - Produce impact summary listing each affected account, resources, and required actions
    - Return `action_required: false` with "no action required" summary when no affected resources found
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x]* 5.2 Write unit tests for impact analyzer
    - Create/update `phd_notification_classifier/tests/test_impact_analyzer.py`
    - Test BREAKING_CHANGE with prod accounts: verify high risk and action required
    - Test BREAKING_CHANGE with only non-prod accounts: verify lower risk
    - Test no affected resources: verify `action_required: false`
    - Test mixed environments: verify prod gets higher risk than non-prod
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

  - [x]* 5.3 Write property test: Impact analysis with environment-based risk scoring
    - **Property 11: Impact analysis covers all affected accounts with environment-based risk scoring**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random BREAKING_CHANGE notifications with affected accounts spanning both production and non-production environments
    - Assert impact summary lists all affected accounts with resources and actions; production accounts receive higher risk scores than non-production
    - **Validates: Requirements 9.1, 9.2, 9.3**

- [x] 6. Cost Estimator Tool
  - [x] 6.1 Implement `phd_notification_classifier/tools/cost_estimator.py`
    - Implement `estimate_cost(notification: dict, affected_accounts: list) -> dict` decorated with `@tool`
    - Produce projected cost impact per affected account
    - Aggregate per-account costs into organization-wide total
    - Track historical cost data for similar events to improve projection accuracy
    - Return `projectable: false` with reason when cost projection cannot be determined
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x]* 6.2 Write unit tests for cost estimator
    - Create/update `phd_notification_classifier/tests/test_cost_estimator.py`
    - Test determinable costs: verify per-account projections and org total
    - Test unknown cost projection: verify `projectable: false` with reason
    - Test historical tracking: verify historical data stored after processing
    - Test multi-account aggregation: verify org total equals sum of per-account costs
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x]* 6.3 Write property test: Cost projections aggregate correctly
    - **Property 12: Cost projections per account aggregate to org total**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random COST_IMPLICATION notifications with 1–10 affected accounts and determinable per-account costs
    - Assert org-wide total equals sum of per-account projected costs
    - **Validates: Requirements 10.1, 10.2**

- [x] 7. SNS Notifier Tool
  - [x] 7.1 Implement `phd_notification_classifier/tools/sns_notifier.py`
    - Implement `publish_to_sns(notification_summary: dict) -> dict` decorated with `@tool`
    - Read the SNS topic ARN from the `SNS_TOPIC_ARN` environment variable
    - Publish a structured JSON payload via boto3 SNS client `publish()` containing: notification_id, event_type, affected_service, classification, reason, impact_analysis, cost_projection, affected_accounts
    - If `SNS_TOPIC_ARN` is not set, log a warning and return `{"status": "skipped", "reason": "SNS_TOPIC_ARN not configured"}`
    - If SNS publish fails, log the failure and return `{"status": "failed", "error": "<details>"}`
    - On success, return `{"status": "sent", "message_id": "<id>"}`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [x]* 7.2 Write unit tests for SNS notifier
    - Create `phd_notification_classifier/tests/test_sns_notifier.py`
    - Test successful publish: mock boto3 SNS client; verify publish called with structured JSON payload containing all required fields
    - Test missing SNS_TOPIC_ARN: unset env var; verify warning logged and `{"status": "skipped"}` returned
    - Test publish failure: mock SNS publish raising exception; verify failure logged and `{"status": "failed", "error": "..."}` returned
    - Test payload structure: verify SNS message contains notification_id, event_type, affected_service, classification, reason, impact_analysis, cost_projection, and affected_accounts
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [x]* 7.3 Write property test: SNS publish contains required fields
    - **Property 15: SNS publish contains required fields**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random completed classification results with various notification types
    - Assert the SNS publish payload contains notification_id, event_type, affected_service, classification, reason, impact_analysis, cost_projection, and affected_accounts
    - Tests the SNS_Notifier in isolation with mocked boto3 SNS client
    - **Validates: Requirements 12.1, 12.2, 12.6**

- [x] 8. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. System Prompt and Agent Entry Point
  - [x] 9.1 Create/update the system prompt in `phd_notification_classifier/prompts.py`
    - Include classification rules: BREAKING_CHANGE for service deprecations/API removals/endpoint retirements, COST_IMPLICATION for extended support fees/pricing changes, SECURITY_RELATED for vulnerabilities/compliance/patches
    - Include priority rule: evaluate BREAKING_CHANGE first, then COST_IMPLICATION, then SECURITY_RELATED
    - Include workflow orchestration: parse payload → get_account_context for each affected account → consolidate_notifications → classify → analyze_impact/estimate_cost → publish_to_sns → return JSON
    - Include output JSON schema with all required fields: notification_id, classification, reason, event_type, affected_service, affected_accounts, environment_breakdown, impact_analysis, cost_projection, total_count, breaking_change_count, cost_implication_count, security_related_count, sns_publish_status
    - Include empty response schema and error response schema
    - _Requirements: 4.1, 4.2, 5.1, 5.2, 6.1, 6.2, 7.1, 7.2, 7.3, 8.1, 8.2, 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 9.2 Implement the agent entry point in `phd_notification_classifier/agent.py`
    - Import `Agent` from `strands`, `BedrockAgentCoreApp` from `bedrock_agentcore`
    - Import all tool functions: `get_account_context`, `consolidate_notifications`, `analyze_impact`, `estimate_cost`, `publish_to_sns`
    - Create `BedrockAgentCoreApp` instance
    - Create `Agent` with `model="us.anthropic.claude-sonnet-4-20250514"`, system prompt from `prompts.py`, and all 5 tools
    - Implement `build_prompt(payload)` to extract health event data and optional `limit` parameter from the payload
    - Implement `@app.entrypoint` async function `classify_notifications(payload)` that calls `build_prompt(payload)` and streams agent response via `agent.stream_async(prompt)`
    - Validate incoming payload; return error response for malformed/unparseable payloads
    - Add `if __name__ == "__main__": app.run()` for local execution
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.5, 2.6_

- [x] 10. Event filtering and limit parameter logic
  - [x] 10.1 Implement status filtering and limit parameter in agent workflow
    - Ensure the agent (via system prompt or build_prompt) filters received events to include only those with status "open" or "upcoming"
    - Implement limit parameter support: when `limit` is a positive integer, process at most that many notifications; when `limit` is 0 or omitted, process all
    - Return error response for empty or invalid health event payloads
    - _Requirements: 2.3, 2.5, 2.6_

  - [x]* 10.2 Write property test: Only open or upcoming events pass status filter
    - **Property 6: Only open or upcoming events pass the status filter**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random lists of event dicts with status codes drawn from `["open", "upcoming", "closed", "unknown", "resolved"]`
    - Assert all processed events have status in `{"open", "upcoming"}` and no other statuses present
    - **Validates: Requirements 2.3**

  - [ ]* 10.3 Write property test: All affected accounts enriched and processed
    - **Property 7: All affected accounts enriched and processed before classification**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random health event payloads with 1–10 affected account IDs
    - Assert `get_account_context` is called for each affected account and all accounts appear in the classification output
    - Tests with mocked Organizations API
    - **Validates: Requirements 2.4, 13.1**

  - [x]* 10.4 Write property test: Limit parameter caps notification count
    - **Property 18: Limit parameter caps notification count**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random notification lists of size 1–20 and random limit values (0, 1, 5, 10, 50)
    - Assert when limit > 0 and limit < len(notifications), the output contains at most `limit` notifications; when limit is 0 or omitted, all notifications are processed
    - **Validates: Requirements 2.6**

- [x] 11. Checkpoint
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Agent classification property tests (LLM-dependent)
  - [x]* 12.1 Write property test: Breaking changes classified BREAKING_CHANGE
    - **Property 1: Breaking changes are classified BREAKING_CHANGE**
    - In `phd_notification_classifier/tests/test_properties_agent.py`
    - Generate random notification dicts with descriptions from templates that unambiguously describe breaking changes (e.g., "{service} will be deprecated and will no longer function after {date}", "Applications will fail to connect to {service} after {date}")
    - Invoke the agent, assert classification is BREAKING_CHANGE
    - **Validates: Requirements 4.1, 4.2**

  - [x]* 12.2 Write property test: Cost implications classified COST_IMPLICATION
    - **Property 2: Cost implications are classified COST_IMPLICATION**
    - In `phd_notification_classifier/tests/test_properties_agent.py`
    - Generate random notification dicts with descriptions that unambiguously describe cost implications (e.g., "Extended support for {service} version {version} will incur additional charges after {date}") and explicitly no breaking change language
    - Invoke the agent, assert classification is COST_IMPLICATION
    - **Validates: Requirements 5.1, 5.2**

  - [x]* 12.3 Write property test: Security events classified SECURITY_RELATED
    - **Property 3: Security events are classified SECURITY_RELATED**
    - In `phd_notification_classifier/tests/test_properties_agent.py`
    - Generate random notification dicts with descriptions that unambiguously describe security concerns (e.g., "A security vulnerability has been identified in {service} requiring immediate patching") and explicitly no breaking change or cost language
    - Invoke the agent, assert classification is SECURITY_RELATED
    - **Validates: Requirements 6.1, 6.2**

  - [x]* 12.4 Write property test: Classification is mutually exclusive with priority ordering
    - **Property 4: Classification is mutually exclusive with priority ordering**
    - In `phd_notification_classifier/tests/test_properties_agent.py`
    - Generate random notification dicts with descriptions containing both breaking change AND cost implication language
    - Invoke the agent, assert classification is BREAKING_CHANGE (priority rule) and exactly one of {BREAKING_CHANGE, COST_IMPLICATION, SECURITY_RELATED}
    - **Validates: Requirements 8.1, 8.2**

  - [x]* 12.5 Write property test: Every classification includes a valid reason
    - **Property 5: Every classification includes a valid reason referencing notification attributes**
    - In `phd_notification_classifier/tests/test_properties_agent.py`
    - Generate random notification dicts with any content (breaking, cost, security, or mixed)
    - Invoke the agent, assert `reason` is non-empty, at least one sentence (>10 chars), and references at least one of: service, event type, or substring from description
    - **Validates: Requirements 7.1, 7.2, 7.3**

- [x] 13. Output format and integration property tests
  - [x]* 13.1 Write property test: Output contains all required fields
    - **Property 13: Output contains all required fields**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random lists of classified notification dicts (1–10 notifications)
    - Assert every entry contains keys: `notification_id`, `classification`, `reason`, `event_type`, `affected_service`, `affected_accounts`, `environment_breakdown` — all non-null
    - Assert output contains `total_count`, `breaking_change_count`, `cost_implication_count`, `security_related_count` with correct tallies
    - **Validates: Requirements 11.1, 11.2, 11.3**

  - [x]* 13.2 Write property test: Classification-specific analysis included in output
    - **Property 14: Classification-specific analysis included in output**
    - In `phd_notification_classifier/tests/test_properties_tool.py`
    - Generate random classified notifications with BREAKING_CHANGE and COST_IMPLICATION types
    - Assert BREAKING_CHANGE entries have non-null `impact_analysis`; COST_IMPLICATION entries have non-null `cost_projection`
    - **Validates: Requirements 11.4, 11.5**

- [x] 14. Agent integration tests
  - [x]* 14.1 Write agent integration tests with known classification examples
    - Create/update `phd_notification_classifier/tests/test_agent_integration.py`
    - Test known BREAKING_CHANGE example: use the Keyspaces TLS certificate notification payload, verify BREAKING_CHANGE classification with impact analysis and SNS publish
    - Test known COST_IMPLICATION example: use the EKS extended support notification payload, verify COST_IMPLICATION classification with cost projection
    - Test known SECURITY_RELATED example: construct a security vulnerability notification payload, verify SECURITY_RELATED classification
    - Test empty payload: provide an empty health event; verify the agent returns the error response schema
    - Test malformed payload: provide invalid JSON; verify the agent returns an error response
    - Test limit parameter: provide a payload with multiple notifications and limit=1; verify only 1 notification processed
    - Test SNS_TOPIC_ARN not set: unset env var; verify agent completes classification and returns `sns_publish_status: "skipped"`
    - _Requirements: 1.3, 2.5, 2.6, 4.1, 5.1, 6.1, 9.1, 10.1, 11.1, 12.1, 12.4_

- [x] 15. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Properties 1–5 require invoking the Strands Agent with the LLM; use clearly unambiguous descriptions for deterministic classification
- Properties 6–12, 15–18 test individual tool functions in isolation (no LLM needed) and run at full speed with 100+ iterations
- Properties 13–14 test output structure and can be validated against the output schema
- For CI/CD, LLM-dependent property tests (1–5) can run with smaller iteration count (e.g., 20) to manage cost/latency; deterministic property tests (6–18) run full 100+ iterations
