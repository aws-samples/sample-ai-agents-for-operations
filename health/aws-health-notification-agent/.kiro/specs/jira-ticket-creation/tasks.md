# Implementation Plan: Jira Ticket Creation

## Overview

Add automatic Jira ticket creation to the `aha_eventbridge_lambda` handler for confirmed-impact health notifications. Three new modules (`jira_client.py`, `ticket_mapper.py`, `team_router.py`) are created, the handler is extended with Jira integration logic, and the SAM template gains Jira-related parameters and IAM permissions. All Jira failures fall back to the existing SNS notification path.

## Tasks

- [x] 1. Create `team_router.py` with assignee resolution logic
  - [x] 1.1 Implement `resolve_assignee()` in `aha_eventbridge_lambda/team_router.py`
    - Match affected service against `service_team_map`; if no match, check affected accounts' OUs against `ou_team_map`; if still no match, return `default_assignee`
    - _Requirements: 3.1, 3.2, 3.3_

  - [ ]* 1.2 Write property test for team routing priority chain (Property 2)
    - **Property 2: Team routing follows priority chain**
    - Generate random service names, OU lists, service-to-team maps, OU-to-team maps, and default assignees; verify service match takes priority over OU match, OU match takes priority over default
    - Create `aha_eventbridge_lambda/tests/test_properties_team_router.py`
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [ ]* 1.3 Write unit tests for `resolve_assignee()`
    - Test exact service match, OU fallback, default fallback, empty maps, multiple accounts with different OUs
    - Create `aha_eventbridge_lambda/tests/test_team_router.py`
    - _Requirements: 3.1, 3.2, 3.3_

- [x] 2. Create `ticket_mapper.py` with notification-to-Jira field mapping
  - [x] 2.1 Implement `map_notification_to_jira_fields()`, `format_description_wiki()`, `_sanitize_label()`, and `RISK_TO_PRIORITY` in `aha_eventbridge_lambda/ticket_mapper.py`
    - Summary: `"{classification}: {affected_service}"` truncated to 255 chars
    - Description: Jira wiki markup with sections (Event Details, Classification, Affected Accounts table, Impact Analysis, Remediation Steps as numbered list)
    - Priority: high→Highest, medium→High, low→Medium
    - Labels: classification, affected_service, "phd-auto-created", event ARN label (sanitized)
    - Include cost_projection in description when present
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 6.1, 6.2, 6.3_

  - [ ]* 2.2 Write property test for ticket mapper completeness (Property 1)
    - **Property 1: Ticket mapper produces complete and correctly structured fields**
    - Generate random notification dicts with varying risk levels, account counts, remediation step counts, optional cost_projection; verify summary length ≤255, required labels present, priority mapping correct, all sections in description, cost_projection included iff present
    - Create `aha_eventbridge_lambda/tests/test_properties_ticket_mapper.py`
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 6.1, 6.2, 6.3**

  - [ ]* 2.3 Write property test for description round-trip (Property 8)
    - **Property 8: Description formatting round-trip**
    - Generate random notification dicts, format to wiki markup, parse back, verify key fields (event ARN, classification, affected accounts, remediation steps) are equivalent
    - Add to `aha_eventbridge_lambda/tests/test_properties_ticket_mapper.py`
    - **Validates: Requirements 6.4**

  - [ ]* 2.4 Write unit tests for `ticket_mapper`
    - Test known input/output pairs, edge cases: empty remediation steps, missing cost_projection, very long service names (>255 char truncation), special characters in labels
    - Create `aha_eventbridge_lambda/tests/test_ticket_mapper.py`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 6.1, 6.2, 6.3_

- [x] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Create `jira_client.py` with Jira REST API client
  - [x] 4.1 Implement `JiraClient` class in `aha_eventbridge_lambda/jira_client.py`
    - `__init__` with base_url, user_email, api_token; Basic Auth header construction
    - `from_config()` factory: retrieve API token from Secrets Manager (boto3, `region_name` explicit), parse team mappings from secret JSON, return configured client
    - `create_issue(fields)`: POST to `/rest/api/2/issue`, raise `JiraApiError` on non-2xx
    - `search_issues(jql, fields, max_results)`: GET `/rest/api/2/search`
    - `find_duplicate(project_key, event_arn_label)`: search for open issues with matching event ARN label; return issue key if found, None otherwise; log warning and return None on search failure
    - Define `JiraApiError` exception class and `JiraConfig` TypedDict
    - _Requirements: 4.1, 4.2, 7.1, 7.2, 7.3_

  - [ ]* 4.2 Write property test for Basic Auth header (Property 7)
    - **Property 7: Basic Auth header is correctly formed**
    - Generate random email/token strings; verify Authorization header equals `"Basic " + base64encode(email + ":" + token)`
    - Create `aha_eventbridge_lambda/tests/test_properties_jira_client.py`
    - **Validates: Requirements 4.2**

  - [ ]* 4.3 Write property test for duplicate detection (Property 9)
    - **Property 9: Duplicate event ARN prevents ticket creation**
    - Generate random event ARNs; mock search returning existing keys; verify `find_duplicate` returns the existing key and no new ticket is created
    - Add to `aha_eventbridge_lambda/tests/test_properties_jira_client.py`
    - **Validates: Requirements 7.2**

  - [ ]* 4.4 Write unit tests for `JiraClient`
    - Test `from_config` with mocked Secrets Manager (success and failure), `create_issue` with mocked HTTP responses (success and error), `find_duplicate` returning existing key / None / raising exception
    - Create `aha_eventbridge_lambda/tests/test_jira_client.py`
    - _Requirements: 4.1, 4.2, 4.3, 7.1, 7.2, 7.3_

- [x] 5. Integrate Jira ticket creation into `handler.py`
  - [x] 5.1 Add `_load_jira_config()` helper to `aha_eventbridge_lambda/handler.py`
    - Read all Jira env vars (JIRA_BASE_URL, JIRA_PROJECT_KEY, JIRA_ISSUE_TYPE, JIRA_USER_EMAIL, JIRA_SECRET_ARN, JIRA_DEFAULT_ASSIGNEE, JIRA_TEAM_MAPPINGS)
    - Return `JiraConfig` dict if all required values present, else log warning and return None
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 5.2 Add Jira creation logic to the `_has_remediation_actions` branch in `handler.py`
    - Before the SES email block: load Jira config, if present iterate confirmed notifications, build JiraClient via `from_config()`, check for duplicates via `find_duplicate()`, resolve assignee via `resolve_assignee()`, map fields via `map_notification_to_jira_fields()`, call `create_issue()`, collect issue keys
    - On any Jira failure: log error, fall back to existing SNS path
    - Pass collected issue keys into SES email body and SNS summary
    - _Requirements: 1.1, 1.2, 1.3, 5.3_

  - [ ]* 5.3 Write property test: one ticket per confirmed notification (Property 3)
    - **Property 3: One Jira ticket per confirmed impact notification**
    - Generate classification results with 1–5 confirmed notifications; mock JiraClient; verify `create_issue` called exactly N times
    - Add to `aha_eventbridge_lambda/tests/test_properties_handler.py`
    - **Validates: Requirements 1.1**

  - [ ]* 5.4 Write property test: Jira failure falls back to SNS (Property 4)
    - **Property 4: Jira failure falls back to SNS**
    - Generate classification results with confirmed impact; mock JiraClient to raise; verify SNS publish is still called
    - Add to `aha_eventbridge_lambda/tests/test_properties_handler.py`
    - **Validates: Requirements 1.2**

  - [ ]* 5.5 Write property test: missing config falls back to SNS (Property 5)
    - **Property 5: Missing Jira config falls back to SNS**
    - Generate random subsets of missing Jira env vars; verify handler skips Jira and publishes to SNS without error
    - Add to `aha_eventbridge_lambda/tests/test_properties_handler.py`
    - **Validates: Requirements 5.3**

  - [ ]* 5.6 Write property test: issue key propagates to SES and SNS (Property 6)
    - **Property 6: Successful Jira issue key propagates to SES and SNS**
    - Generate random issue keys from mocked JiraClient; verify issue key appears in both SES email body and SNS summary
    - Add to `aha_eventbridge_lambda/tests/test_properties_handler.py`
    - **Validates: Requirements 1.3**

  - [ ]* 5.7 Write unit tests for handler Jira integration
    - Test handler with mocked JiraClient: successful creation, Jira failure fallback, missing config fallback, duplicate detection skip, issue key in SES/SNS
    - Add to `aha_eventbridge_lambda/tests/test_handler.py`
    - _Requirements: 1.1, 1.2, 1.3, 5.3_

- [x] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Update SAM template with Jira configuration
  - [x] 7.1 Add Jira parameters and environment variables to `aha_eventbridge_lambda/template.yaml`
    - Add parameters: `JiraBaseUrl`, `JiraProjectKey`, `JiraIssueType` (default "Bug"), `JiraUserEmail`, `JiraSecretArn`, `JiraDefaultAssignee`, `JiraTeamMappings` (default "{}")
    - Add environment variables to `HealthEventFunction`: map all Jira parameters
    - Add IAM policy statement: `secretsmanager:GetSecretValue` on `JiraSecretArn`
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests use `hypothesis` with `@settings(max_examples=100)`
- All Jira failures are non-fatal — the existing SES + SNS flow always completes
- Run tests with: `.venv/bin/python3.13 -m pytest aha_eventbridge_lambda/tests/ -v`
