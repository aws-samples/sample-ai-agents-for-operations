# Requirements Document

## Introduction

This feature implements an AWS Lambda function that serves as a glue layer between Amazon EventBridge and the phd-notification-classifier agent running on AWS AgentCore. The Lambda receives AWS Health events forwarded by an existing AWS Health Aware (AHA) deployment via EventBridge rules. Based on the event type category, the Lambda invokes the AgentCore Runtime endpoint for issue, investigation and scheduled changes events, then constructs a human-readable summary from the agent's classification result and publishes it to an SNS topic. For confirmed-impact events, the Lambda creates Jira tickets (via `jira_client.py`, `ticket_mapper.py`, `team_router.py`) and sends Slack webhook notifications. When `REMEDIATION_MODE=approval`, the Lambda sends SES approval emails with an "Approve" button. For account notifications, the Lambda publishes a summary directly to the SNS topic. All event types ultimately result in an SNS notification, ensuring the team receives actionable summaries via email. The Lambda uses the event ARN as the AgentCore session ID to enable session continuity when the same health event updates over time. An idempotency check via DynamoDB conditional put prevents duplicate processing. The AgentCore Runtime is managed as a CloudFormation resource (`AWS::BedrockAgentCore::Runtime`) within the SAM template. The function is deployed via `deploy.sh` (no SAM CLI needed) with Python 3.13 and boto3.

## Glossary

- **Lambda**: The AWS Lambda function that receives EventBridge events and routes them to the appropriate downstream handler.
- **EventBridge**: The Amazon EventBridge service that delivers AHA health events to the Lambda via configured rules.
- **AHA**: AWS Health Aware, the existing deployment that forwards AWS Health events to EventBridge.
- **Health_Event**: An AWS Health event delivered to the Lambda via EventBridge, containing event metadata such as event ARN, status code, affected accounts, event type category, and event description.
- **Event_ARN**: The Amazon Resource Name uniquely identifying a specific AWS Health event, used as the session identifier for AgentCore invocations.
- **Event_Type_Category**: The category of a Health_Event. One of "issue", "investigation", "scheduledChange", or "accountNotification".
- **AgentCore_Runtime**: The AWS AgentCore execution environment that hosts the phd-notification-classifier agent.
- **AgentCore_Endpoint**: The AgentCore Runtime endpoint identified by the AGENT_RUNTIME_ENDPOINT_ARN environment variable.
- **InvokeAgentRuntime_API**: The boto3 API call (`bedrock-agentcore:InvokeAgentRuntime`) used to invoke the AgentCore_Runtime endpoint with a JSON payload and receive a streaming response.
- **SNS_Topic**: The Amazon SNS topic (`aha-health-event-notifications`) created as a resource within the SAM_Template, referenced by the Lambda via the SNS_TOPIC_ARN environment variable.
- **Streaming_Response**: The chunked response returned by the AgentCore_Runtime when the Lambda invokes the InvokeAgentRuntime_API.
- **Dead_Letter_Queue**: An SQS queue configured to receive events that the Lambda fails to process after all retry attempts.
- **SAM_Template**: The AWS SAM template file that defines the Lambda function, EventBridge rule, Dead_Letter_Queue, and associated IAM permissions.
- **Agent_Classification_Result**: The structured JSON output returned by the phd-notification-classifier agent, embedded within the final text of the Streaming_Response (typically inside a markdown code block). The agent's primary output format uses a "notifications" array, where each entry contains "classification", "urgency", "deadline", "reason", "affected_service", "affected_accounts", and optional "impact_analysis" and "cost_projection". Classification values are one of: SERVICE_DISRUPTION, BREAKING_CHANGE, SECURITY_RELATED, COST_IMPLICATION, INFORMATIONAL, or UNCLASSIFIED. The output includes counts for all six categories. The Lambda also supports a legacy single-result format using "classification_category" and "classification_reason" keys. The response text may be double-JSON-encoded and requires unwrapping before JSON extraction.
- **Human_Readable_Summary**: A plain-text formatted summary constructed from the Agent_Classification_Result, designed for human consumption via email notifications. Includes the classification category, urgency, deadline, reason, affected service, affected accounts with environment types, all six category counts, and conditional sections for impact analysis and cost projection when present.
- **Summary_Formatter**: The module (`summary_formatter.py`) responsible for converting an Agent_Classification_Result dict into a Human_Readable_Summary. Handles both the full agent output format (with "notifications" array) and the legacy single-result format, and accepts both key naming conventions ("classification"/"reason" and "classification_category"/"classification_reason"). Includes urgency, deadline, and all six category counts (service_disruption_count, breaking_change_count, security_related_count, cost_implication_count, informational_count, unclassified_count).
- **Response_Parser**: The module (`response_parser.py`) responsible for unwrapping double-JSON-encoded AgentCore responses and extracting JSON classification results from markdown text. Separated from the handler for testability.
- **Jira_Client**: The module (`jira_client.py`) that creates Jira tickets for confirmed-impact events using the Jira REST API with Basic Auth from Secrets Manager.
- **Ticket_Mapper**: The module (`ticket_mapper.py`) that maps notification fields to Jira issue fields with wiki markup and Team field routing.
- **Team_Router**: The module (`team_router.py`) that resolves the Jira assignee using multi-level routing: resource → account → service → OU → default.
- **Idempotency_Check**: A DynamoDB conditional put using the event ARN as the key to prevent duplicate processing of the same health event.
- **Deploy_Script**: The `deploy.sh` script that handles the full deployment (build, package, deploy) without requiring SAM CLI to be installed separately.

## Requirements

### Requirement 1: EventBridge Event Reception

**User Story:** As a platform operator, I want the Lambda to receive AHA health events from EventBridge, so that health events are automatically processed without manual intervention.

#### Acceptance Criteria

1. WHEN EventBridge delivers a Health_Event, THE Lambda SHALL accept and parse the event payload.
2. THE Lambda SHALL extract the Event_ARN, status code, affected accounts, Event_Type_Category, and event description from each Health_Event.
3. IF a Health_Event is missing the Event_ARN or Event_Type_Category, THEN THE Lambda SHALL log the malformed event and raise an error.
4. WHEN the Health_Event Detail field is a JSON string (as in AHA-forwarded events), THE Lambda SHALL parse the JSON string into a dict before extracting fields.
5. WHEN the Health_Event eventDescription field is a dict with a latestDescription key (as in AHA-forwarded events), THE Lambda SHALL extract the description from the dict, in addition to supporting the standard list-of-dicts format from raw AWS Health events.
6. WHEN the Health_Event affectedEntities include awsAccountName, entityUrl, or entityMetadata fields (as in AHA-forwarded events), THE Lambda SHALL preserve these fields in the parsed output alongside the standard awsAccountId and entityValue fields.

### Requirement 2: Event Routing by Type Category

**User Story:** As a platform operator, I want health events routed to different handlers based on their type category, so that issues, investigations and scheduled changes receive deep agent analysis while account notifications are summarized directly, and all event types ultimately result in an SNS notification.

#### Acceptance Criteria

1. WHEN a Health_Event has an Event_Type_Category of "issue" or "investigation" or "scheduledChange", THE Lambda SHALL invoke the AgentCore_Endpoint using the InvokeAgentRuntime_API and, upon receiving the agent's response, publish a Human_Readable_Summary to the SNS_Topic.
2. WHEN a Health_Event has an Event_Type_Category of "accountNotification", THE Lambda SHALL publish a summary of the Health_Event to the SNS_Topic.
3. IF a Health_Event has an unrecognized Event_Type_Category, THEN THE Lambda SHALL log a warning and publish the Health_Event summary to the SNS_Topic.

### Requirement 3: AgentCore Runtime Invocation

**User Story:** As a platform operator, I want the Lambda to invoke the AgentCore agent with health event details, so that the phd-notification-classifier agent can perform classification and impact analysis.

#### Acceptance Criteria

1. WHEN the Lambda invokes the AgentCore_Endpoint, THE Lambda SHALL pass the Health_Event as a JSON payload to the InvokeAgentRuntime_API.
2. THE Lambda SHALL use the Event_ARN as the session ID in the InvokeAgentRuntime_API call to enable session continuity for subsequent updates to the same Health_Event.
3. THE Lambda SHALL include the event description, status code, affected accounts, and Event_Type_Category in the JSON payload sent to the AgentCore_Endpoint.

### Requirement 4: Streaming Response Handling

**User Story:** As a platform operator, I want the Lambda to handle the streaming response from AgentCore, so that the agent's analysis results are captured and logged.

#### Acceptance Criteria

1. WHEN the AgentCore_Endpoint returns a Streaming_Response, THE Lambda SHALL read all chunks of the Streaming_Response to completion.
2. WHEN the Lambda finishes reading the Streaming_Response, THE Lambda SHALL log the final assembled result.
3. WHEN the assembled Streaming_Response is double-JSON-encoded (the text value is wrapped in quotes with escaped characters), THE Lambda SHALL unwrap the outer JSON encoding to obtain the raw text before further processing.
4. WHEN the Lambda obtains the raw text from the Streaming_Response, THE Lambda SHALL extract the Agent_Classification_Result by locating and parsing the JSON object embedded within the text (which may be wrapped in a markdown code block), using brace-matching to identify the JSON boundaries.
5. IF the Streaming_Response is interrupted before completion, THEN THE Lambda SHALL log the partial response received and raise an error.

### Requirement 5: SNS Summary Publication

**User Story:** As a platform operator, I want account notifications published to an SNS topic, so that my team receives summaries of non-critical health events without invoking the agent.

#### Acceptance Criteria

1. WHEN the Lambda publishes a summary to the SNS_Topic, THE Lambda SHALL include the Event_ARN, Event_Type_Category, affected accounts, and event description in the SNS message.
2. THE Lambda SHALL format the SNS message as a JSON object.
3. THE Lambda SHALL set the SNS message subject to include the Event_Type_Category and the affected AWS service name.
4. IF the SNS publish operation fails, THEN THE Lambda SHALL log the failure details and raise an error.

### Requirement 6: Error Handling and Retries for AgentCore Invocation

**User Story:** As a platform operator, I want transient AgentCore failures retried automatically, so that temporary service disruptions do not cause missed health event processing.

#### Acceptance Criteria

1. IF the InvokeAgentRuntime_API call fails with a transient error (throttling, timeout, or 5xx response), THEN THE Lambda SHALL retry the invocation using exponential backoff with a maximum of 3 retry attempts.
2. IF all retry attempts for an InvokeAgentRuntime_API call are exhausted, THEN THE Lambda SHALL log the final failure details and raise an error to trigger Dead_Letter_Queue delivery.
3. THE Lambda SHALL distinguish between transient errors (eligible for retry) and permanent errors (not eligible for retry) when handling InvokeAgentRuntime_API failures.
4. IF a permanent error occurs during the InvokeAgentRuntime_API call, THEN THE Lambda SHALL log the error details and raise an error immediately without retrying.

### Requirement 7: Dead-Letter Queue Configuration

**User Story:** As a platform operator, I want failed events sent to a dead-letter queue, so that no health events are silently lost and I can investigate and reprocess failures.

#### Acceptance Criteria

1. THE SAM_Template SHALL configure a Dead_Letter_Queue as an SQS queue associated with the Lambda function.
2. WHEN the Lambda fails to process a Health_Event after all retry attempts, THE Dead_Letter_Queue SHALL receive the failed event.
3. THE SAM_Template SHALL grant the Lambda function permission to send messages to the Dead_Letter_Queue.

### Requirement 8: Environment Variable Configuration

**User Story:** As a platform operator, I want the Lambda configured via environment variables, so that I can deploy the same code across different environments without code changes.

#### Acceptance Criteria

1. THE Lambda SHALL read the AgentCore_Endpoint ARN from the AGENT_RUNTIME_ENDPOINT_ARN environment variable.
2. THE Lambda SHALL read the SNS topic ARN from the SNS_TOPIC_ARN environment variable, which references the stack-created SNS_Topic via the SAM_Template intrinsic function `!Ref HealthEventSnsTopic`.
3. THE Lambda SHALL read the logging level from the LOG_LEVEL environment variable.
4. IF the AGENT_RUNTIME_ENDPOINT_ARN environment variable is not set, THEN THE Lambda SHALL fail initialization with a descriptive error message.
5. IF the SNS_TOPIC_ARN environment variable is not set, THEN THE Lambda SHALL fail initialization with a descriptive error message.
6. WHEN the LOG_LEVEL environment variable is not set, THE Lambda SHALL default to "INFO" logging level.

### Requirement 9: Lambda Timeout and Runtime Configuration

**User Story:** As a platform operator, I want the Lambda timeout set to 900 seconds, so that long-running agent investigations have sufficient time to complete.

#### Acceptance Criteria

1. THE SAM_Template SHALL configure the Lambda function timeout to 900 seconds.
2. THE SAM_Template SHALL configure the Lambda function runtime as Python 3.13.
3. THE SAM_Template SHALL configure the Lambda function memory to a minimum of 256 MB.

### Requirement 10: AWS SAM Deployment Template

**User Story:** As a platform operator, I want the infrastructure defined as a SAM template, so that the Lambda and its dependencies can be deployed and managed as infrastructure-as-code.

#### Acceptance Criteria

1. THE SAM_Template SHALL define the Lambda function with an EventBridge rule as the event source.
2. THE SAM_Template SHALL define the Dead_Letter_Queue as an SQS queue resource.
3. THE SAM_Template SHALL define an IAM policy granting the Lambda function the `bedrock-agentcore:InvokeAgentRuntime` action on the AgentCore_Endpoint resource, using a wildcard suffix (`${AgentRuntimeEndpointArn}*`) to cover sub-resources appended at invocation time (e.g., `/runtime-endpoint/DEFAULT`).
4. THE SAM_Template SHALL define an IAM policy granting the Lambda function permission to publish messages to the SNS_Topic.
5. THE SAM_Template SHALL define an IAM policy granting the Lambda function permission to send messages to the Dead_Letter_Queue.
6. THE SAM_Template SHALL define the AgentCore Runtime as a CloudFormation resource (`AWS::BedrockAgentCore::Runtime`) within the template, including the container URI, IAM role, environment variables (`SNS_TOPIC_ARN`, `BEDROCK_MODEL_ID`, `SYSTEM_PROMPT_S3_BUCKET`, `SYSTEM_PROMPT_S3_KEY`), and network configuration.
7. THE SAM_Template SHALL define outputs for the SNS_Topic ARN, the Lambda function ARN, the Dead_Letter_Queue URL, and the AgentCore Runtime ARN.
8. THE SAM_Template SHALL define the EventBridge rule to match AWS Health events with source "aws.health".
9. THE SAM_Template SHALL configure the Lambda handler as `aha_eventbridge_lambda.handler.handler` to reflect the package directory structure with `CodeUri: ../`.
10. THE SAM_Template SHALL define a second EventBridge rule to match AHA-forwarded events with source "aha", so that the Lambda receives events from both the raw AWS Health event bus and the AHA event bus.
11. A `deploy.sh` script SHALL handle the full deployment lifecycle (build, package, deploy) without requiring SAM CLI to be installed separately by the operator.

### Requirement 11: Structured Logging

**User Story:** As a platform operator, I want structured logging throughout the Lambda execution, so that I can trace event processing and troubleshoot failures in CloudWatch Logs.

#### Acceptance Criteria

1. THE Lambda SHALL log each received Health_Event with the Event_ARN and Event_Type_Category at INFO level.
2. THE Lambda SHALL log the routing decision (AgentCore invocation or SNS publication) for each Health_Event at INFO level.
3. THE Lambda SHALL log the start and completion of each AgentCore invocation with the Event_ARN and session ID at INFO level.
4. THE Lambda SHALL log all errors with the Event_ARN, error type, and error message at ERROR level.
5. THE Lambda SHALL use structured JSON format for all log entries.

### Requirement 12: Agent Result Summary Publication

**User Story:** As a platform operator, I want the Lambda to publish a human-readable summary of the agent's classification result to the SNS topic after processing issue and investigation events, so that my team receives actionable notifications for all health event types via email.

#### Acceptance Criteria

1. WHEN the Lambda extracts an Agent_Classification_Result from the Streaming_Response, THE Lambda SHALL construct a Human_Readable_Summary from the Agent_Classification_Result.
2. THE Human_Readable_Summary SHALL include the classification category (BREAKING_CHANGE, COST_IMPLICATION, or SECURITY_RELATED), classification reason, affected service, and affected accounts with their environment types.
3. THE Summary_Formatter SHALL accept both the agent's primary key names ("classification" and "reason") and the legacy key names ("classification_category" and "classification_reason") when reading the Agent_Classification_Result.
4. WHERE the Agent_Classification_Result contains a "notifications" array, THE Summary_Formatter SHALL format each notification entry from the array, including classification, reason, affected service, affected accounts with environment types, and environment breakdown counts.
5. WHERE the Agent_Classification_Result contains an impact analysis, THE Human_Readable_Summary SHALL include the impact analysis summary, risk level, and action-required status.
6. WHERE the Agent_Classification_Result contains a cost projection, THE Human_Readable_Summary SHALL include the projected cost details.
7. THE Lambda SHALL format the Human_Readable_Summary as plain text suitable for human consumption via email notifications, not as raw JSON.
8. WHEN the Lambda constructs the Human_Readable_Summary, THE Lambda SHALL publish the Human_Readable_Summary to the SNS_Topic.
9. THE Lambda SHALL set the SNS message subject to include the classification category and the affected service name from the Agent_Classification_Result, truncated to a maximum of 100 characters to comply with the SNS subject length limit.
10. IF the Lambda fails to parse the assembled Streaming_Response into an Agent_Classification_Result, THEN THE Lambda SHALL publish the raw assembled response to the SNS_Topic with a subject indicating a parsing warning, truncating the message body to stay within the SNS 256 KB message size limit.
11. IF the SNS publish operation for the Human_Readable_Summary fails, THEN THE Lambda SHALL log the failure details and raise an error.

### Requirement 13: Idempotency Check

**User Story:** As a platform operator, I want duplicate health events to be detected and skipped, so that the same event is not processed multiple times.

#### Acceptance Criteria

1. WHEN the Lambda receives a Health_Event, THE Lambda SHALL perform an idempotency check using a DynamoDB conditional put with the Event_ARN as the key.
2. IF the conditional put succeeds (first time processing), THE Lambda SHALL proceed with normal event processing.
3. IF the conditional put fails (duplicate event), THE Lambda SHALL log the duplicate and return a success response without further processing.
4. THE idempotency records SHALL have a TTL of 24 hours.

### Requirement 14: Jira Ticket Creation for Confirmed-Impact Events

**User Story:** As a platform operator, I want Jira tickets automatically created for confirmed-impact health events, so that my team has trackable work items for remediation.

#### Acceptance Criteria

1. WHEN the Agent_Classification_Result contains notifications classified as SERVICE_DISRUPTION, BREAKING_CHANGE, or SECURITY_RELATED, THE Lambda SHALL create a Jira ticket for each such notification.
2. THE Lambda SHALL use `jira_client.py` to interact with the Jira REST API using Basic Auth credentials from Secrets Manager.
3. THE Lambda SHALL use `ticket_mapper.py` to map notification fields to Jira issue fields.
4. THE Lambda SHALL use `team_router.py` to resolve the assignee using multi-level routing (resource → account → service → OU → default).
5. THE Lambda SHALL check for duplicate Jira tickets before creating a new one (using the event ARN as a deduplication key).
6. IF Jira configuration is incomplete (missing required env vars), THE Lambda SHALL skip Jira integration and log an informational message.

### Requirement 15: Slack Webhook Notifications

**User Story:** As a platform operator, I want Slack notifications for confirmed-impact health events, so that my team is alerted in real-time.

#### Acceptance Criteria

1. WHEN the Agent_Classification_Result contains notifications classified as SERVICE_DISRUPTION, BREAKING_CHANGE, or SECURITY_RELATED, THE Lambda SHALL post a notification to the configured Slack webhook URL.
2. THE Lambda SHALL include the subject, summary, and event ARN in the Slack notification.
3. IF the SLACK_WEBHOOK_URL environment variable is not set, THE Lambda SHALL skip Slack notification.
4. IF the Slack notification fails, THE Lambda SHALL log the failure and continue processing (non-blocking).

### Requirement 16: SES Approval Emails

**User Story:** As a platform operator, I want approval emails sent for confirmed-impact events when remediation mode is "approval", so that remediation actions require human authorization.

#### Acceptance Criteria

1. WHEN `REMEDIATION_MODE=approval` AND the Agent_Classification_Result contains confirmed-impact notifications with remediation steps, THE Lambda SHALL send an approval email via SES.
2. THE approval email SHALL include the notification summary, remediation actions, and an "Approve" button URL.
3. THE Lambda SHALL generate a unique approval token and store it in DynamoDB with the remediation payload.
4. IF SES email sending fails, THE Lambda SHALL fall back to publishing the summary to SNS.
5. IF `REMEDIATION_MODE=notification`, THE Lambda SHALL skip the approval email flow and publish directly to SNS.
