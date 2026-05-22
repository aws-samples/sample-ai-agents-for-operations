# Implementation Plan: AHA EventBridge Lambda

## Overview

Implement an AWS Lambda function that receives AWS Health events from EventBridge and routes them based on event type category. Issue/investigation events are sent to AgentCore Runtime for classification, then the agent's result is parsed, formatted as a human-readable summary, and published to SNS. Scheduled changes and account notifications are published as JSON summaries directly to SNS. All event types result in an SNS notification. Deployed via AWS SAM with Python 3.13 and boto3. Implementation proceeds bottom-up: event parser → AgentCore invoker → SNS publisher → summary formatter → handler → SAM template.

## Tasks

- [x] 1. Implement event parser module
  - [x] 1.1 Create `aha_eventbridge_lambda/event_parser.py` with `parse_health_event()` function
    - Extract `event_arn`, `status_code`, `affected_accounts`, `event_type_category`, `event_description`, and `service` from the EventBridge event `detail` section
    - Derive `affected_accounts` as unique account IDs from `detail.affectedEntities`
    - Extract `event_description` from `detail.eventDescription[0].latestDescription`
    - Raise `ValueError` if `eventArn` or `eventTypeCategory` is missing
    - _Requirements: 1.1, 1.2, 1.3_

  - [x]* 1.2 Write property test for event parsing (Property 1)
    - **Property 1: Event parsing extracts all required fields**
    - Generate random valid EventBridge payloads with varying ARNs, categories, accounts, descriptions, and services using Hypothesis
    - Verify the parser extracts all six fields with values matching the original payload
    - **Validates: Requirements 1.1, 1.2**

  - [x]* 1.3 Write property test for malformed event rejection (Property 2)
    - **Property 2: Malformed events are rejected**
    - Generate EventBridge payloads with missing `eventArn`, missing `eventTypeCategory`, or both
    - Verify `ValueError` is raised in all cases
    - **Validates: Requirements 1.3**

- [x] 2. Implement AgentCore invoker module
  - [x] 2.1 Create `aha_eventbridge_lambda/agentcore_invoker.py` with `invoke_agentcore()` function
    - Construct JSON payload with event description, status code, affected accounts, and event type category
    - Use event ARN as session ID in the `invoke_agent_runtime()` boto3 call
    - Read all chunks from the streaming response and assemble the full result string
    - Log partial response and raise error if stream is interrupted
    - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4_

  - [x] 2.2 Implement retry logic with exponential backoff in `agentcore_invoker.py`
    - Classify errors as transient (throttling, timeout, 5xx) or permanent (4xx except throttling)
    - Retry transient errors up to 3 times with exponential backoff (1s, 2s, 4s)
    - Raise immediately on permanent errors without retrying
    - Log final failure details when all retries are exhausted
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x]* 2.3 Write property test for AgentCore payload completeness (Property 4)
    - **Property 4: AgentCore payload contains all required event fields**
    - Generate random parsed events routed to AgentCore
    - Verify the payload JSON contains event description, status code, affected accounts, and event type category
    - **Validates: Requirements 3.1, 3.3**

  - [x]* 2.4 Write property test for session ID (Property 5)
    - **Property 5: Session ID equals event ARN**
    - Generate random parsed events with varying ARNs
    - Verify the session ID in the AgentCore call matches the event ARN
    - **Validates: Requirements 3.2**

  - [x]* 2.5 Write property test for streaming response assembly (Property 6)
    - **Property 6: Streaming response is fully assembled**
    - Generate random lists of byte chunks using Hypothesis
    - Verify the assembled result equals the concatenation of all chunk payloads
    - **Validates: Requirements 4.1, 4.2**

  - [x]* 2.6 Write property test for retry behavior (Property 8)
    - **Property 8: Transient errors are retried, permanent errors are not**
    - Generate random transient error sequences (1-3 failures then success, or all failures)
    - Verify retry count matches expected behavior and permanent errors are never retried
    - **Validates: Requirements 6.1, 6.3, 6.4**

- [x] 3. Implement SNS publisher module
  - [x] 3.1 Create `aha_eventbridge_lambda/sns_publisher.py` with `publish_to_sns()` function
    - Format SNS message as JSON object containing event ARN, event type category, affected accounts, event description, service, and status code
    - Set SNS message subject to `"{event_type_category}: {service}"` format
    - Log failure details and raise error if SNS publish fails
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 3.2 Add `publish_summary_to_sns()` function to `aha_eventbridge_lambda/sns_publisher.py`
    - Publish a plain-text Human_Readable_Summary as the SNS message body
    - Accept `summary`, `subject`, `topic_arn`, and `event_arn` parameters
    - Log and raise on publish failure
    - _Requirements: 12.6, 12.7, 12.9_

  - [x]* 3.3 Write property test for SNS message completeness (Property 7)
    - **Property 7: SNS message contains all required fields as JSON**
    - Generate random parsed events routed to SNS (scheduledChange/accountNotification)
    - Verify message body is valid JSON with all required fields and subject contains category and service
    - **Validates: Requirements 5.1, 5.2, 5.3**

- [x] 4. Implement summary formatter module
  - [x] 4.1 Create `aha_eventbridge_lambda/summary_formatter.py` with `format_summary()` function
    - Accept an Agent_Classification_Result dict and return a plain-text Human_Readable_Summary string
    - Always include: classification category, classification reason, affected service, affected accounts with environment types
    - Conditionally include impact analysis section (summary, risk level, action required) only when present in input
    - Conditionally include cost projection section only when present in input
    - Return plain text (not JSON) suitable for email consumption
    - Handle missing optional fields gracefully without errors
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5_

  - [x]* 4.2 Write property test for summary formatter completeness (Property 10)
    - **Property 10: Summary formatter includes all required and conditional fields as plain text**
    - Generate random Agent_Classification_Result dicts with varying combinations of required fields and optional fields (impact analysis, cost projection)
    - Verify output is plain text (not valid JSON) containing all required fields
    - Verify impact analysis section is present only when input includes it
    - Verify cost projection section is present only when input includes it
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5**

  - [x]* 4.3 Write unit tests for summary formatter edge cases
    - Test Agent_Classification_Result with no impact analysis or cost projection produces summary with only required sections
    - Test Agent_Classification_Result with impact analysis but no cost projection
    - Test Agent_Classification_Result with cost projection but no impact analysis
    - Test Agent_Classification_Result with both impact analysis and cost projection
    - _Requirements: 12.2, 12.3, 12.4_

- [x] 5. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement Lambda handler and wire components together
  - [x] 6.1 Create `aha_eventbridge_lambda/handler.py` with `handler()` entry point
    - Read `AGENT_RUNTIME_ENDPOINT_ARN` and `SNS_TOPIC_ARN` at module level (fail fast with `KeyError` if missing)
    - Read `LOG_LEVEL` with default `"INFO"`
    - Configure structured JSON logging at the specified level
    - Parse incoming EventBridge event using `parse_health_event()`
    - Route to `invoke_agentcore()` for "issue"/"investigation" categories
    - After AgentCore invocation: parse JSON response into Agent_Classification_Result
    - Pass classification result to `format_summary()` to produce Human_Readable_Summary
    - Publish Human_Readable_Summary to SNS via `publish_summary_to_sns()` with subject `"{classification_category}: {affected_service}"`
    - If AgentCore response is not valid JSON: publish raw response to SNS with warning subject `"WARNING: Unparseable agent response for {event_arn}"`
    - Route to `publish_to_sns()` for "scheduledChange"/"accountNotification" categories
    - Route unrecognized categories to `publish_to_sns()` with a warning log
    - Log event receipt, routing decision, invocation start/completion, and errors as structured JSON
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 4.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 11.1, 11.2, 11.3, 11.4, 11.5, 12.1, 12.6, 12.7, 12.8, 12.9_

  - [x]* 6.2 Write property test for routing logic (Property 3)
    - **Property 3: Routing is determined by event type category**
    - Generate random parsed events with categories from known set plus random strings
    - Verify "issue"/"investigation" → AgentCore invocation followed by SNS summary publication
    - Verify "scheduledChange"/"accountNotification" → SNS JSON publication
    - Verify unrecognized categories → SNS JSON publication
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [x]* 6.3 Write property test for structured JSON logging (Property 9)
    - **Property 9: All log entries are structured JSON with required context**
    - Generate random events and error scenarios, capture log output
    - Verify each log entry is valid JSON with event ARN and event type category (INFO) or event ARN, error type, and error message (ERROR)
    - **Validates: Requirements 11.1, 11.2, 11.4, 11.5**

  - [x]* 6.4 Write property test for agent summary SNS publication (Property 11)
    - **Property 11: Agent result summary is published to SNS with classification-based subject**
    - Generate random issue/investigation events with valid AgentCore JSON responses containing Agent_Classification_Result
    - Verify the handler publishes the Human_Readable_Summary to SNS with a subject containing the classification category and affected service name
    - **Validates: Requirements 12.6, 12.7**

  - [x]* 6.5 Write property test for unparseable response fallback (Property 12)
    - **Property 12: Unparseable agent response falls back to raw publication with warning**
    - Generate random non-JSON strings as AgentCore responses
    - Verify the handler publishes the raw string to SNS with a subject indicating a parsing warning
    - **Validates: Requirements 12.8**

  - [x]* 6.6 Write unit tests for handler integration
    - Test happy path: issue event → AgentCore invocation → JSON parsing → summary formatting → SNS summary publication → success
    - Test happy path: investigation event with impact analysis and cost projection → summary contains all sections
    - Test happy path: scheduledChange event → SNS JSON publication → success
    - Test AgentCore returns non-JSON response → raw response published to SNS with warning subject
    - Test Agent_Classification_Result with no impact analysis or cost projection → summary with only required sections
    - Test SNS publish failure for Human_Readable_Summary raises error
    - Test edge cases: empty affected accounts, empty event description, single-chunk streaming response
    - Test edge cases: stream interruption, SNS publish failure for direct summary, all retries exhausted
    - Test environment variables: missing AGENT_RUNTIME_ENDPOINT_ARN raises KeyError, missing SNS_TOPIC_ARN raises KeyError, missing LOG_LEVEL defaults to "INFO"
    - Test unrecognized category logs warning and publishes to SNS
    - _Requirements: 1.1, 2.1, 2.2, 2.3, 4.3, 5.4, 6.2, 8.4, 8.5, 8.6, 12.6, 12.7, 12.8, 12.9_

- [x] 7. Create AWS SAM template
  - [x] 7.1 Create `aha_eventbridge_lambda/template.yaml` with Lambda function, EventBridge rule, SNS topic, and DLQ
    - Define `HealthEventSnsTopic` as an `AWS::SNS::Topic` resource with topic name `aha-health-event-notifications`
    - Define Lambda function with `python3.13` runtime, 900s timeout, 256 MB memory
    - Set handler to `aha_eventbridge_lambda.handler.handler` with `CodeUri: ../`
    - Define EventBridge rule matching `source: aws.health` events
    - Define SQS dead-letter queue and associate with Lambda
    - Accept `AgentRuntimeEndpointArn` as a template parameter
    - Set `SNS_TOPIC_ARN` environment variable via `!Ref HealthEventSnsTopic`
    - Define IAM policy for `bedrock-agentcore:InvokeAgentRuntime` with resource `!Sub "${AgentRuntimeEndpointArn}*"`
    - Define IAM policy for `sns:Publish` on the SNS topic
    - Define IAM policy for `sqs:SendMessage` on the DLQ
    - Define outputs for SNS topic ARN, Lambda function ARN, and DLQ URL
    - _Requirements: 7.1, 7.2, 7.3, 9.1, 9.2, 9.3, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9_

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use the `hypothesis` library with `@settings(max_examples=100)`
- All boto3 clients should be mocked in tests using `unittest.mock`
- Checkpoints ensure incremental validation between implementation phases
- The summary formatter (task 4) is a new component that must be implemented before the handler (task 6) since the handler depends on it
