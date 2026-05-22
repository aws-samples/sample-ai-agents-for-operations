# Implementation Plan: Human Approval Remediation

## Overview

Add a human-in-the-loop approval workflow to the PHD Notification Classifier. When the agent confirms impact with remediation steps, the Notification_Lambda generates approval tokens, stores them in DynamoDB, and sends HTML emails via SES with clickable "Approve" buttons. An Approval Lambda validates tokens, invokes the agent in remediation mode, and sends confirmation emails. The existing SNS plain-text path remains for non-remediation notifications.

## Tasks

- [x] 1. SAM template — add new infrastructure resources
  - [x] 1.1 Add parameters and DynamoDB Approval_Store table to `infra/template.yaml`
    - Add `SesIdentityArn` and `NotificationRecipientEmail` parameters
    - Add `ApprovalStore` DynamoDB table with partition key `token` (String), TTL attribute `expires_at`, on-demand billing
    - _Requirements: 9.1, 9.4, 7.1, 8.5_
  - [x] 1.2 Add Approval API Gateway and Approval Lambda to `infra/template.yaml`
    - Add `ApprovalApi` HTTP API (API Gateway v2) with GET `/approve` route
    - Add `ApprovalFunction` Lambda (Python 3.13, 900s timeout) with environment variables: `APPROVAL_TABLE_NAME`, `AGENT_RUNTIME_ENDPOINT_ARN`, `SES_SENDER_IDENTITY`, `NOTIFICATION_RECIPIENT_EMAIL`
    - Add `ApprovalFunctionRole` IAM role scoped to DynamoDB read/write on ApprovalStore, AgentCore InvokeAgentRuntime, SES SendEmail, CloudWatch Logs
    - _Requirements: 9.2, 9.3, 8.1, 8.4_
  - [x] 1.3 Modify HealthEventFunction in `infra/template.yaml` for approval flow
    - Add environment variables: `APPROVAL_TABLE_NAME`, `SES_SENDER_IDENTITY`, `APPROVAL_API_URL`, `NOTIFICATION_RECIPIENT_EMAIL`
    - Add IAM permissions for DynamoDB PutItem on ApprovalStore and SES SendEmail
    - _Requirements: 9.5_
  - [x] 1.4 Add outputs for new resources
    - Add outputs for `ApprovalApiUrl`, `ApprovalTableName`, `ApprovalFunctionName`

- [x] 2. Token Generator — create `aha_eventbridge_lambda/token_generator.py`
  - [x] 2.1 Implement `generate_approval_token()` and `store_approval_record()`
    - `generate_approval_token()` uses `secrets.token_urlsafe(48)` to produce a URL-safe token with ≥256 bits entropy
    - `store_approval_record()` writes to DynamoDB with `attribute_not_exists(token)` condition, sets `status` to `pending`, computes `expires_at` as creation time + 7 days (604800 seconds), retries up to 3 times on token collision
    - Read `APPROVAL_TABLE_NAME` and `APPROVAL_API_URL` from environment variables
    - Return dict with `token`, `expires_at` (ISO 8601), and `approval_url`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 8.2_
  - [ ]* 2.2 Write property test for token generation (Property 1)
    - **Property 1: Token generation produces unique, URL-safe, high-entropy tokens**
    - **Validates: Requirements 1.1, 1.3, 8.2**
    - Create `aha_eventbridge_lambda/tests/test_properties_token_generator.py`
    - Generate batches of tokens, assert each is URL-safe, ≥48 chars, and all unique
  - [ ]* 2.3 Write property test for token storage round-trip (Property 2)
    - **Property 2: Token storage round-trip preserves all fields**
    - **Validates: Requirements 1.2**
    - Use moto to mock DynamoDB; generate random payloads, contexts, emails; verify round-trip field preservation and initial `pending` status
  - [ ]* 2.4 Write property test for token expiry calculation (Property 3)
    - **Property 3: Token expiry is exactly 7 days from creation**
    - **Validates: Requirements 1.4**
    - Generate random creation timestamps, verify `expires_at` equals creation + 604800 seconds

- [x] 3. SES Notifier — create `aha_eventbridge_lambda/ses_notifier.py`
  - [x] 3.1 Implement `build_html_email()`, `send_approval_email()`, and `send_confirmation_email()`
    - `build_html_email()` generates HTML with styled approval buttons (anchor elements with `href`) and includes action description + expiry for each action
    - `send_approval_email()` sends via SES with HTML body and plain-text fallback containing raw URLs
    - `send_confirmation_email()` sends success/failure confirmation with original notification context (event ARN, service, accounts)
    - Read `SES_SENDER_IDENTITY` from environment variable
    - _Requirements: 2.4, 2.5, 3.1, 3.2, 3.4, 6.1, 6.2, 6.3, 6.4_
  - [ ]* 3.2 Write property test for approval URL format (Property 5)
    - **Property 5: Approval URL format**
    - **Validates: Requirements 2.2**
    - Create `aha_eventbridge_lambda/tests/test_properties_ses_notifier.py`
    - Generate random domains and tokens, verify URL matches `https://{domain}/approve?token={token}`
  - [ ]* 3.3 Write property test for email content completeness (Property 6)
    - **Property 6: Approval email contains action description and expiry for each action**
    - **Validates: Requirements 2.4, 2.5**
    - Generate random approval action lists, verify both HTML and plain-text contain every description and expiry
  - [ ]* 3.4 Write property test for HTML structure and plain-text fallback (Property 7)
    - **Property 7: HTML email contains clickable buttons and plain-text fallback contains raw URLs**
    - **Validates: Requirements 3.1, 3.2**
    - Generate random approval action lists, verify HTML contains `<a>` elements with correct `href`, plain-text contains raw URLs
  - [ ]* 3.5 Write property test for confirmation email content (Property 11)
    - **Property 11: Confirmation email includes status, context, and correct recipient**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4**
    - Generate random execution results and notification contexts, verify email contains status, actions/errors, and original context

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Modify Notification_Lambda — update `aha_eventbridge_lambda/handler.py`
  - [x] 5.1 Add routing logic for remediation-eligible notifications
    - Implement `_has_remediation_actions(classification_result)` — returns True when any notification has `impact_status == "confirmed"` and non-empty `suggested_next_steps`
    - Implement `_extract_remediation_actions(classification_result)` — extracts individual remediation actions with description, remediation_payload, and notification_context
    - _Requirements: 2.1, 2.3_
  - [x] 5.2 Integrate token generation and SES sending into the handler
    - After AgentCore response parsing, check `_has_remediation_actions()`
    - If true: generate tokens via `token_generator`, send HTML email via `ses_notifier`, fall back to SNS on any error
    - If false: continue existing SNS plain-text path
    - Read new environment variables: `APPROVAL_TABLE_NAME`, `SES_SENDER_IDENTITY`, `APPROVAL_API_URL`, `NOTIFICATION_RECIPIENT_EMAIL`
    - _Requirements: 2.1, 2.2, 2.3, 3.3_
  - [ ]* 5.3 Write property test for notification routing (Property 4)
    - **Property 4: Notification routing — confirmed impact with remediation goes to SES, otherwise to SNS**
    - **Validates: Requirements 2.1, 2.3, 3.3**
    - Create property tests in `aha_eventbridge_lambda/tests/test_properties_handler.py` (append to existing file)
    - Generate random classification results with/without confirmed impact, verify routing decision
  - [ ]* 5.4 Write unit tests for handler approval flow
    - Create tests in `aha_eventbridge_lambda/tests/test_handler.py` (append to existing file)
    - Test SES path triggered for confirmed impact with remediation steps
    - Test SNS fallback when SES fails
    - Test SNS path for non-remediation notifications
    - _Requirements: 2.1, 2.3, 3.3_

- [x] 6. Approval Lambda — create `approval_lambda/handler.py`
  - [x] 6.1 Implement token validation and approval endpoint
    - Create `approval_lambda/` directory with `__init__.py` and `handler.py`
    - Implement `handler(event, context)` — extract `token` from query parameters, return 400 if missing
    - Implement `_validate_and_approve_token(token)` — DynamoDB conditional update with `ConditionExpression: attribute_exists(token) AND #s = :pending AND expires_at > :now`; raise `TokenExpiredError`, `TokenAlreadyUsedError`, or `TokenNotFoundError` as appropriate
    - Return HTML response pages: 200 (approved), 410 (expired), 409 (already used), 404 (not found), 500 (error)
    - Log every approval attempt with token prefix (first 8 chars), source IP, timestamp, and outcome
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 7.2, 8.3_
  - [x] 6.2 Implement remediation invocation and status updates
    - Implement `_invoke_remediation(endpoint_arn, remediation_payload)` — invoke AgentCore with `remediation_action` payload, 900s timeout
    - On success: update DynamoDB status to `executed`, store execution result
    - On failure/timeout: update DynamoDB status to `failed`, store error details
    - Send confirmation email via `ses_notifier.send_confirmation_email()` to the original recipient
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4_
  - [ ]* 6.3 Write property test for token validation state machine (Property 8)
    - **Property 8: Token validation state machine**
    - **Validates: Requirements 4.2, 4.3, 4.4, 4.5, 7.2**
    - Create `approval_lambda/tests/test_properties_handler.py`
    - Use moto DynamoDB; generate tokens in various states (pending, expired, used, missing); verify correct HTTP status codes
  - [ ]* 6.4 Write property test for post-remediation status (Property 10)
    - **Property 10: Post-remediation status reflects execution outcome**
    - **Validates: Requirements 5.3, 5.4**
    - Generate random agent responses (success/failure), verify DynamoDB status transitions to `executed` or `failed` with correct stored results
  - [ ]* 6.5 Write unit tests for Approval Lambda
    - Create `approval_lambda/tests/test_handler.py`
    - Test each HTTP response code (200, 400, 404, 409, 410, 500) with specific token states
    - Test missing token query parameter returns 400
    - Test concurrent approval simulation (Property 9: atomic approval prevents double-execution)
    - Test AgentCore invocation timeout handling
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.3, 5.4_

- [x] 7. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Agent remediation mode — modify `phd_notification_classifier/agent.py` and `phd_notification_classifier/prompts.py`
  - [x] 8.1 Add remediation prompt to `phd_notification_classifier/prompts.py`
    - Add `REMEDIATION_PROMPT_SUFFIX` constant instructing the agent to execute the specified remediation actions via MCP tools and return a structured JSON result with `status`, `actions_taken`, and `error`
    - Add `build_remediation_prompt(remediation_payload)` function that combines the suffix with the specific remediation payload
    - _Requirements: 10.1, 10.2, 10.3_
  - [x] 8.2 Add remediation routing to `phd_notification_classifier/agent.py`
    - In `classify_notifications()`, check if payload contains `remediation_action` key
    - If present: build remediation prompt via `build_remediation_prompt()`, invoke agent with it, yield structured result
    - If absent: continue existing classification flow unchanged
    - _Requirements: 10.1, 10.2, 10.3_
  - [ ]* 8.3 Write property test for agent remediation mode routing (Property 12)
    - **Property 12: Agent remediation mode routing**
    - **Validates: Requirements 10.1**
    - Create `phd_notification_classifier/tests/test_properties_remediation.py`
    - Generate random payloads with/without `remediation_action` key, verify correct routing to remediation vs classification mode
  - [ ]* 8.4 Write unit tests for remediation prompt and routing
    - Add tests to `phd_notification_classifier/tests/` for `build_remediation_prompt()` output structure
    - Test agent routes to remediation mode with `remediation_action` payload
    - Test agent routes to classification mode without `remediation_action` key
    - Test error result when MCP tools are unavailable
    - _Requirements: 10.1, 10.2, 10.3_

- [x] 9. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use `hypothesis` with `@settings(max_examples=100)` and `moto` for DynamoDB/SES mocking
- Run tests with `.venv/bin/python3.13 -m pytest`
- Checkpoints ensure incremental validation between major components
