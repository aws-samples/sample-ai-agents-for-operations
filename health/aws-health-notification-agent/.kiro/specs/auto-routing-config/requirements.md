# Requirements Document

## Introduction

The PHD Notification Classifier system currently relies on manually configured team routing mappings (service-to-team and OU-to-team) stored in Secrets Manager and environment variables. When routing rules need to change, an engineer must manually edit JSON and redeploy. This feature introduces an automated routing configuration pipeline: a team uploads a routing document (CSV, JSON, or free-text) to an S3 bucket, a Lambda invokes Bedrock (Claude) to generate structured routing JSON, sends the result to Slack for human review, and upon approval writes the validated config to Secrets Manager. The existing health event handler picks up the new routing config on its next invocation without redeployment.

## Glossary

- **Routing_Document**: A file (CSV, JSON, or free-text) uploaded to the Routing_Config_Bucket that describes team-to-service and team-to-OU assignments.
- **Routing_Config_Bucket**: An S3 bucket that receives Routing_Document uploads and emits S3 event notifications.
- **Routing_Config_Lambda**: A Lambda function triggered by S3 object-created events that reads the Routing_Document, invokes Bedrock to generate structured routing JSON, and sends the result to Slack.
- **Routing_JSON**: A structured JSON object containing `by_service`, `by_ou`, and `default` mappings that the existing Jira integration consumes as `service_team_map` and `ou_team_map`.
- **Approval_Lambda**: A Lambda function that handles Slack interactive payloads (approve button clicks) and writes the approved Routing_JSON to Secrets Manager.
- **Slack_Webhook**: An incoming webhook URL used by the Routing_Config_Lambda to post the generated Routing_JSON to a Slack channel with an Approve button.
- **Slack_Interactive_Endpoint**: An API Gateway endpoint that receives Slack interactive message payloads when a user clicks the Approve button.
- **Jira_Secret**: The existing Secrets Manager secret (referenced by `JIRA_SECRET_ARN`) that stores the Jira API token and team routing mappings (`service_team_map`, `ou_team_map`).
- **Bedrock_Model**: The Claude model invoked via Amazon Bedrock to parse unstructured routing documents into Routing_JSON (e.g., `eu.anthropic.claude-sonnet-4-20250514-v1:0`).

## Requirements

### Requirement 1: S3 Upload Trigger

**User Story:** As a team lead, I want to upload a routing document to an S3 bucket and have the system automatically process it, so that I do not need to manually edit JSON configuration.

#### Acceptance Criteria

1. WHEN a new object is created in the Routing_Config_Bucket, THE Routing_Config_Lambda SHALL be invoked with the S3 event containing the bucket name and object key.
2. THE Routing_Config_Bucket SHALL accept objects with `.csv`, `.json`, and `.txt` file extensions.
3. IF the uploaded object has an unsupported file extension, THEN THE Routing_Config_Lambda SHALL log a warning with the object key and file extension, and skip processing.
4. THE Routing_Config_Lambda SHALL read the full content of the uploaded S3 object into memory for processing.

### Requirement 2: LLM-Based Routing JSON Generation

**User Story:** As a team lead, I want the system to use an LLM to interpret my routing document regardless of format, so that I can provide routing information in CSV, JSON, or plain English.

#### Acceptance Criteria

1. WHEN the Routing_Config_Lambda reads a valid Routing_Document, THE Routing_Config_Lambda SHALL invoke the Bedrock_Model with the document content and a prompt instructing it to produce Routing_JSON.
2. THE Bedrock_Model invocation SHALL produce a Routing_JSON object containing three top-level keys: `by_service` (mapping service names to Jira assignee account IDs), `by_ou` (mapping OU names or paths to Jira assignee account IDs), and `default` (a single fallback Jira assignee account ID).
3. WHEN the Bedrock_Model returns a response, THE Routing_Config_Lambda SHALL validate that the response is valid JSON and contains the required `by_service`, `by_ou`, and `default` keys.
4. IF the Bedrock_Model response fails JSON validation, THEN THE Routing_Config_Lambda SHALL retry the invocation once with an error-correction prompt appended, and if the retry also fails, log an error and stop processing.
5. THE Routing_Config_Lambda SHALL pass `region_name="eu-west-1"` explicitly when creating the Bedrock Runtime boto3 client.

### Requirement 3: Slack Review Notification

**User Story:** As a team lead, I want to review the generated routing JSON in Slack before it takes effect, so that I can catch errors before they affect ticket routing.

#### Acceptance Criteria

1. WHEN the Routing_Config_Lambda produces valid Routing_JSON, THE Routing_Config_Lambda SHALL send a Slack message to the configured channel via the Slack_Webhook containing the formatted Routing_JSON and an "Approve" interactive button.
2. THE Slack message SHALL include: the original file name, a formatted code block showing the Routing_JSON, a summary of the number of service mappings, OU mappings, and the default assignee, and an "Approve" button.
3. THE Slack message SHALL include a "Reject" button that dismisses the message and logs the rejection without writing to Secrets Manager.
4. THE Routing_Config_Lambda SHALL read the Slack_Webhook URL from an environment variable named `SLACK_WEBHOOK_URL`.
5. IF the Slack message delivery fails, THEN THE Routing_Config_Lambda SHALL log an error with the HTTP status code and response body, and stop processing.

### Requirement 4: Slack Approval and Secrets Manager Write

**User Story:** As a team lead, I want the approved routing JSON to be written to Secrets Manager when I click Approve in Slack, so that the routing config is updated without manual intervention.

#### Acceptance Criteria

1. WHEN a user clicks the "Approve" button in Slack, THE Slack interactive payload SHALL be sent to the Slack_Interactive_Endpoint handled by the Approval_Lambda.
2. WHEN the Approval_Lambda receives a valid Slack interactive payload with an "approve" action, THE Approval_Lambda SHALL read the Routing_JSON from the payload and write the `service_team_map` and `ou_team_map` fields to the Jira_Secret in Secrets Manager.
3. THE Approval_Lambda SHALL perform a read-modify-write on the Jira_Secret: reading the current secret value, merging the new `service_team_map` and `ou_team_map` keys, preserving all other existing keys (including `jira_api_token`), and writing the updated secret back.
4. WHEN the Secrets Manager write succeeds, THE Approval_Lambda SHALL respond to Slack with a confirmation message indicating the routing config has been updated, including a timestamp.
5. IF the Secrets Manager write fails, THEN THE Approval_Lambda SHALL respond to Slack with an error message containing the error details, and log the full error.
6. THE Approval_Lambda SHALL pass `region_name="eu-west-1"` explicitly when creating the Secrets Manager boto3 client.

### Requirement 5: Slack Payload Verification

**User Story:** As a platform engineer, I want Slack interactive payloads to be verified, so that unauthorized requests cannot modify the routing configuration.

#### Acceptance Criteria

1. WHEN the Approval_Lambda receives a request, THE Approval_Lambda SHALL verify the Slack request signature using the signing secret stored in an environment variable named `SLACK_SIGNING_SECRET`.
2. IF the Slack signature verification fails, THEN THE Approval_Lambda SHALL return an HTTP 401 response and log a warning with the request source IP.
3. THE Approval_Lambda SHALL verify that the Slack request timestamp is within 5 minutes of the current time to prevent replay attacks.

### Requirement 6: Routing JSON Compatibility

**User Story:** As a platform engineer, I want the generated routing JSON to be compatible with the existing Jira integration, so that the health event handler can consume it without code changes.

#### Acceptance Criteria

1. THE Routing_JSON `by_service` mapping SHALL be written to the Jira_Secret under the key `service_team_map`, matching the format consumed by the existing `JiraClient.from_config` method and `resolve_assignee` function.
2. THE Routing_JSON `by_ou` mapping SHALL be written to the Jira_Secret under the key `ou_team_map`, matching the format consumed by the existing `resolve_assignee` function.
3. THE Routing_JSON `default` value SHALL be written to the Jira_Secret under the key `default_assignee`.
4. FOR ALL valid Routing_JSON objects, writing to Secrets Manager then reading back via `JiraClient.from_config` SHALL produce equivalent `service_team_map` and `ou_team_map` dictionaries (round-trip property).

### Requirement 7: Jira Ticket Creation for Routing Changes

**User Story:** As a team lead, I want a Jira ticket to be created when a routing config change is approved, so that there is an audit trail of configuration changes.

#### Acceptance Criteria

1. WHEN the Approval_Lambda successfully writes the Routing_JSON to Secrets Manager, THE Approval_Lambda SHALL create a Jira ticket in the configured project documenting the routing configuration change.
2. THE Jira ticket SHALL include: the original file name, a summary of changes (services added, removed, or modified), the approver's Slack username, and a timestamp.
3. THE Jira ticket SHALL be assigned to the team specified by the `default` assignee in the Routing_JSON.
4. IF Jira ticket creation fails, THEN THE Approval_Lambda SHALL log a warning and continue, since the Secrets Manager write has already succeeded.

### Requirement 8: Infrastructure Configuration

**User Story:** As a platform engineer, I want the SAM template to include all resources for the auto-routing pipeline, so that deployment is automated and repeatable.

#### Acceptance Criteria

1. THE SAM template SHALL define the Routing_Config_Bucket as an S3 bucket with a `s3:ObjectCreated:*` event notification triggering the Routing_Config_Lambda.
2. THE SAM template SHALL define the Routing_Config_Lambda with IAM permissions for `s3:GetObject` on the Routing_Config_Bucket, `bedrock:InvokeModel` on the Bedrock_Model, and `secretsmanager:GetSecretValue` and `secretsmanager:PutSecretValue` on the Jira_Secret.
3. THE SAM template SHALL define the Approval_Lambda with an API Gateway endpoint for the Slack_Interactive_Endpoint, and IAM permissions for `secretsmanager:GetSecretValue` and `secretsmanager:PutSecretValue` on the Jira_Secret.
4. THE SAM template SHALL include parameters for `SLACK_WEBHOOK_URL`, `SLACK_SIGNING_SECRET`, and `JIRA_SECRET_ARN` with descriptions.
5. THE SAM template SHALL output the Routing_Config_Bucket name, the Slack_Interactive_Endpoint URL, and the Routing_Config_Lambda ARN.
