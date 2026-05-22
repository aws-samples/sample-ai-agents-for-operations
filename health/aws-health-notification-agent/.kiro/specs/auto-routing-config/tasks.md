# Implementation Plan: Auto Routing Config

## Overview

Add an automated routing configuration pipeline with two new Lambda packages (`routing_config_lambda` and `routing_approval_lambda`). An S3 upload triggers Bedrock-based parsing of routing documents into structured JSON, posts to Slack for review, and on approval writes to Secrets Manager and creates an audit Jira ticket. The SAM template is extended with the new resources, parameters, and outputs.

## Tasks

- [x] 1. Set up project structure and data models
  - [x] 1.1 Create `routing_config_lambda` package skeleton
    - Create `routing_config_lambda/__init__.py`, `routing_config_lambda/handler.py`, `routing_config_lambda/s3_reader.py`, `routing_config_lambda/bedrock_invoker.py`, `routing_config_lambda/slack_notifier.py`
    - Create `routing_config_lambda/tests/__init__.py`
    - _Requirements: 1.1, 2.1, 3.1_

  - [x] 1.2 Create `routing_approval_lambda` package skeleton
    - Create `routing_approval_lambda/__init__.py`, `routing_approval_lambda/handler.py`, `routing_approval_lambda/slack_verifier.py`, `routing_approval_lambda/secrets_writer.py`, `routing_approval_lambda/audit_ticket.py`
    - Create `routing_approval_lambda/tests/__init__.py`
    - _Requirements: 4.1, 5.1, 7.1_

  - [x] 1.3 Define shared data models
    - Define `RoutingJson`, `SlackInteractivePayload`, `RoutingReviewMessage`, `SecretPayload` TypedDicts in `routing_config_lambda/models.py`
    - Reuse or import `RoutingJson` and `SecretPayload` in `routing_approval_lambda/models.py`
    - _Requirements: 2.2, 6.1, 6.2, 6.3_


- [x] 2. Implement S3 reader with file extension validation
  - [x] 2.1 Implement `read_routing_document()` in `routing_config_lambda/s3_reader.py`
    - Define `SUPPORTED_EXTENSIONS = {".csv", ".json", ".txt"}`
    - Validate file extension (case-insensitive); return `None` and log warning for unsupported extensions
    - Read S3 object content via boto3 with `region_name="eu-west-1"`
    - _Requirements: 1.2, 1.3, 1.4_

  - [ ]* 2.2 Write property test for file extension validation (Property 1)
    - **Property 1: File extension validation**
    - Generate random file key strings; verify the extension check returns `True` iff the key ends with `.csv`, `.json`, or `.txt` (case-insensitive), `False` for all other extensions
    - Create `routing_config_lambda/tests/test_properties_routing_config.py`
    - **Validates: Requirements 1.2, 1.3**

  - [ ]* 2.3 Write unit tests for `s3_reader`
    - Test supported extensions (.csv, .json, .txt), unsupported extensions (.xlsx, .pdf), case variations (.CSV, .Json), S3 read with mocked boto3 client
    - Create `routing_config_lambda/tests/test_s3_reader.py`
    - _Requirements: 1.2, 1.3, 1.4_

- [x] 3. Implement Bedrock invoker with validation and retry
  - [x] 3.1 Implement `invoke_bedrock()` and `validate_routing_json()` in `routing_config_lambda/bedrock_invoker.py`
    - Build prompt with document content instructing Bedrock to produce JSON with `by_service`, `by_ou`, `default` keys
    - Invoke Bedrock Runtime via boto3 with `region_name="eu-west-1"` and model ID `eu.anthropic.claude-sonnet-4-20250514-v1:0`
    - Parse response, validate JSON structure (required keys, correct types)
    - On validation failure, retry once with error-correction prompt appended; if retry fails, log error and raise
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 3.2 Write property test for routing JSON structure validation (Property 2)
    - **Property 2: Routing JSON structure validation**
    - Generate random dictionaries with/without required keys, with correct/incorrect value types; verify `validate_routing_json` returns `True` iff dict contains `by_service` (dict[str, str]), `by_ou` (dict[str, str]), and `default` (str) with all non-empty string values
    - Add to `routing_config_lambda/tests/test_properties_routing_config.py`
    - **Validates: Requirements 2.2, 2.3**

  - [ ]* 3.3 Write unit tests for `bedrock_invoker`
    - Test valid response parsing, invalid JSON retry (first call garbage, second valid), retry exhaustion (both invalid → error), missing keys detection
    - Create `routing_config_lambda/tests/test_bedrock_invoker.py`
    - _Requirements: 2.1, 2.2, 2.3, 2.4_


- [x] 4. Implement Slack notifier with review message formatting
  - [x] 4.1 Implement `post_routing_review()` in `routing_config_lambda/slack_notifier.py`
    - Format Slack Block Kit message with header, source file name, pretty-printed routing JSON code block, summary (service count, OU count, default assignee), Approve button (style: primary) and Reject button (style: danger)
    - Embed routing JSON in button `value` field (JSON-encoded)
    - Read `SLACK_WEBHOOK_URL` from environment variable
    - POST to webhook; on failure log HTTP status and response body, raise
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 4.2 Write property test for Slack message formatting completeness (Property 3)
    - **Property 3: Slack message formatting completeness**
    - Generate random valid `RoutingJson` objects and non-empty source file names; verify formatted Slack blocks contain: source file name, JSON-encoded routing config, correct service mapping count, correct OU mapping count, default assignee value, an "Approve" action button, and a "Reject" action button
    - Add to `routing_config_lambda/tests/test_properties_routing_config.py`
    - **Validates: Requirements 3.2, 3.3**

  - [ ]* 4.3 Write unit tests for `slack_notifier`
    - Test message block structure, webhook POST success, webhook POST failure (HTTP error logging), missing webhook URL
    - Create `routing_config_lambda/tests/test_slack_notifier.py`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 5. Wire up Routing Config Lambda handler
  - [x] 5.1 Implement `handler()` in `routing_config_lambda/handler.py`
    - Parse S3 event to extract bucket name and object key
    - Call `read_routing_document()` — skip if unsupported extension
    - Call `invoke_bedrock()` with document content
    - Call `post_routing_review()` with routing JSON and source file name
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 3.1_

  - [ ]* 5.2 Write unit tests for routing config handler
    - Test end-to-end flow with mocked S3, Bedrock, and Slack; test unsupported extension skip; test Bedrock failure; test Slack failure
    - Create `routing_config_lambda/tests/test_handler.py`
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 3.1_

- [x] 6. Checkpoint - Ensure routing config lambda tests pass
  - Ensure all tests pass, ask the user if questions arise.


- [x] 7. Implement Slack signature verification
  - [x] 7.1 Implement `verify_slack_signature()` in `routing_approval_lambda/slack_verifier.py`
    - Compute HMAC-SHA256 signature from signing secret, timestamp, and request body
    - Compare computed signature against provided signature using `hmac.compare_digest`
    - Verify timestamp is within 5 minutes of current time to prevent replay attacks
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ]* 7.2 Write property test for Slack signature verification correctness (Property 5)
    - **Property 5: Slack signature verification correctness**
    - Generate random request bodies, timestamps, and signing secrets; compute correct HMAC-SHA256 signature; verify `verify_slack_signature` returns `True` for correct signatures and `False` for incorrect ones
    - Create `routing_approval_lambda/tests/test_properties_routing_approval.py`
    - **Validates: Requirements 5.1, 5.2**

  - [ ]* 7.3 Write property test for timestamp replay protection (Property 6)
    - **Property 6: Timestamp replay protection**
    - Generate random timestamps within ±10 minutes of current time; verify timestamps within 5 minutes are accepted and timestamps beyond 5 minutes are rejected
    - Add to `routing_approval_lambda/tests/test_properties_routing_approval.py`
    - **Validates: Requirements 5.3**

  - [ ]* 7.4 Write unit tests for `slack_verifier`
    - Test valid signature, invalid signature (returns False), stale timestamp (>5 min), edge cases at exactly 5-minute boundary
    - Create `routing_approval_lambda/tests/test_slack_verifier.py`
    - _Requirements: 5.1, 5.2, 5.3_

- [x] 8. Implement Secrets Manager read-modify-write
  - [x] 8.1 Implement `update_routing_config()` in `routing_approval_lambda/secrets_writer.py`
    - Read current Jira secret via `GetSecretValue` with `region_name="eu-west-1"`
    - Merge routing JSON: map `by_service` → `service_team_map`, `by_ou` → `ou_team_map`, `default` → `default_assignee`
    - Preserve all existing keys (including `jira_api_token`)
    - Write updated secret via `PutSecretValue`
    - _Requirements: 4.2, 4.3, 4.6, 6.1, 6.2, 6.3_

  - [ ]* 8.2 Write property test for secret merge preserves existing keys (Property 4)
    - **Property 4: Secret merge preserves existing keys**
    - Generate random existing secret dicts (with arbitrary keys including `jira_api_token`) and valid `RoutingJson` objects; verify merged result contains all original keys, maps routing keys correctly, and preserves non-routing key values
    - Add to `routing_approval_lambda/tests/test_properties_routing_approval.py`
    - **Validates: Requirements 4.2, 4.3**

  - [ ]* 8.3 Write property test for routing JSON round-trip (Property 7)
    - **Property 7: Routing JSON round-trip through Secrets Manager**
    - Generate random valid `RoutingJson` objects; simulate write (map keys) then read-back (reverse map); verify `read_back["service_team_map"] == original["by_service"]`, `read_back["ou_team_map"] == original["by_ou"]`, `read_back["default_assignee"] == original["default"]`
    - Add to `routing_approval_lambda/tests/test_properties_routing_approval.py`
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4**

  - [ ]* 8.4 Write unit tests for `secrets_writer`
    - Test read-modify-write with mocked Secrets Manager, preservation of existing keys, SM read failure, SM write failure
    - Create `routing_approval_lambda/tests/test_secrets_writer.py`
    - _Requirements: 4.2, 4.3, 4.5, 4.6_


- [x] 9. Implement audit Jira ticket creation
  - [x] 9.1 Implement `create_audit_ticket()` in `routing_approval_lambda/audit_ticket.py`
    - Build Jira ticket fields: summary with routing config change description, description with source file name, approver username, change summary (services/OUs added/removed), timestamp
    - Assign ticket to the `default` assignee from the routing JSON
    - Use existing `JiraClient` from `aha_eventbridge_lambda.jira_client`
    - Return issue key on success, `None` on failure (log warning, do not raise)
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 9.2 Write property test for audit ticket field completeness (Property 8)
    - **Property 8: Audit ticket field completeness**
    - Generate random valid `RoutingJson`, non-empty source file names, and non-empty approver usernames; verify generated Jira fields contain: source file name in description, approver username in description, non-empty summary, assignee set to `default` value from routing JSON
    - Add to `routing_approval_lambda/tests/test_properties_routing_approval.py`
    - **Validates: Requirements 7.2, 7.3**

  - [ ]* 9.3 Write unit tests for `audit_ticket`
    - Test successful ticket creation with mocked JiraClient, Jira failure returns None (logs warning), field content verification
    - Create `routing_approval_lambda/tests/test_audit_ticket.py`
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

- [x] 10. Wire up Routing Approval Lambda handler
  - [x] 10.1 Implement `handler()` in `routing_approval_lambda/handler.py`
    - Parse Slack interactive payload from API Gateway event
    - Call `verify_slack_signature()` — return HTTP 401 on failure with source IP logged
    - Extract action: if "reject", log rejection and respond with acknowledgment
    - If "approve": extract routing JSON from button value, call `update_routing_config()`, call `create_audit_ticket()`, respond to Slack with confirmation message including timestamp
    - On Secrets Manager failure: respond to Slack with error message, log full error
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 7.1_

  - [ ]* 10.2 Write unit tests for routing approval handler
    - Test approve flow (mocked SM + Jira), reject flow (no SM write), invalid signature (401), stale timestamp (401), SM write failure (error response), Jira failure (continues after SM success)
    - Create `routing_approval_lambda/tests/test_handler.py`
    - _Requirements: 4.1, 4.2, 4.4, 4.5, 5.1, 5.2, 5.3, 7.4_

- [x] 11. Checkpoint - Ensure routing approval lambda tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Update SAM template with new resources
  - [x] 12.1 Add new parameters and resources to `aha_eventbridge_lambda/template.yaml`
    - Add parameters: `SlackWebhookUrl`, `SlackSigningSecret` (with descriptions)
    - Add `RoutingConfigBucket` S3 bucket with `s3:ObjectCreated:*` event notification triggering `RoutingConfigFunction`
    - Add `RoutingConfigFunction` Lambda (Python 3.13, handler `routing_config_lambda.handler.handler`, CodeUri `../`) with environment variables (`SLACK_WEBHOOK_URL`, `BEDROCK_MODEL_ID`, `JIRA_SECRET_ARN`) and IAM policies (`s3:GetObject` on bucket, `bedrock:InvokeModel` on model, `secretsmanager:GetSecretValue` and `secretsmanager:PutSecretValue` on Jira secret)
    - Add `RoutingApprovalApi` HTTP API and `RoutingApprovalFunction` Lambda (Python 3.13, handler `routing_approval_lambda.handler.handler`, CodeUri `../`) with `POST /slack/interactive` event, environment variables (`SLACK_SIGNING_SECRET`, `JIRA_SECRET_ARN`, `JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_ISSUE_TYPE`, `JIRA_USER_EMAIL`), and IAM policies (`secretsmanager:GetSecretValue` and `secretsmanager:PutSecretValue` on Jira secret)
    - Add outputs: `RoutingConfigBucketName`, `SlackInteractiveEndpointUrl`, `RoutingConfigFunctionArn`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 13. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests use Hypothesis with `@settings(max_examples=100)`
- All 8 design correctness properties are covered by property-based test sub-tasks
- Test helpers use `_prefixed` factory functions per project convention
- Run routing config lambda tests: `.venv/bin/python3.13 -m pytest routing_config_lambda/tests/ -v`
- Run routing approval lambda tests: `.venv/bin/python3.13 -m pytest routing_approval_lambda/tests/ -v`
