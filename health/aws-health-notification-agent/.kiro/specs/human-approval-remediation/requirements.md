# Requirements Document

## Introduction

This feature adds human-in-the-loop approval to the PHD Notification Classifier system. When the agent confirms impact and generates remediation steps (e.g., EKS cluster upgrades), the notification email includes an approval link. Clicking the link triggers the agent to execute the remediation via MCP tools. This ensures no automated write operations occur without explicit human authorization.

## Glossary

- **Approval_Service**: The API Gateway + Lambda subsystem that handles approval requests, validates tokens, and triggers remediation execution.
- **Approval_Token**: A unique, single-use, time-limited cryptographic token that authorizes execution of a specific remediation action.
- **Approval_Store**: A DynamoDB table that persists pending approval records including the token, remediation payload, status, and expiry timestamp.
- **Notification_Lambda**: The existing aha-eventbridge-lambda Lambda function that formats agent output and publishes notification emails.
- **Classifier_Agent**: The PHD Notification Classifier agent running on AgentCore Runtime that classifies health events and generates remediation steps.
- **Remediation_Payload**: A JSON object containing the exact remediation actions to execute (e.g., cluster name, target version, region, MCP tool name).
- **Approval_Status**: The state of an approval record — one of `pending`, `approved`, `executed`, `failed`, or `expired`.
- **SES_Notifier**: The Amazon SES integration that sends HTML-formatted emails with clickable approval links, replacing SNS for remediation-eligible notifications.
- **Confirmation_Email**: An email sent after remediation execution reporting success or failure of the action.

## Requirements

### Requirement 1: Approval Token Generation

**User Story:** As an operator, I want each remediation action to have a unique approval token, so that I can authorize specific actions individually.

#### Acceptance Criteria

1. WHEN the Classifier_Agent confirms impact and generates remediation steps, THE Notification_Lambda SHALL generate a unique Approval_Token for each distinct remediation action.
2. THE Notification_Lambda SHALL store each Approval_Token in the Approval_Store with the associated Remediation_Payload, creation timestamp, expiry timestamp, and an initial Approval_Status of `pending`.
3. THE Notification_Lambda SHALL generate Approval_Tokens using a cryptographically secure random generator producing a URL-safe string of at least 48 characters.
4. WHEN generating an Approval_Token, THE Notification_Lambda SHALL set the expiry timestamp to 7 days from the creation timestamp.

### Requirement 2: Approval Link in Notification Email

**User Story:** As an operator, I want the notification email to include a clickable approval link for each remediation action, so that I can authorize remediation directly from the email.

#### Acceptance Criteria

1. WHEN the Classifier_Agent output contains confirmed impact with remediation steps, THE Notification_Lambda SHALL include a unique approval URL for each remediation action in the notification email.
2. THE Notification_Lambda SHALL format the approval URL as `https://{api_gateway_domain}/approve?token={approval_token}`.
3. WHEN the Classifier_Agent output contains no confirmed impact or no remediation steps, THE Notification_Lambda SHALL send the notification without approval links using the existing SNS plain-text format.
4. THE Notification_Lambda SHALL include a human-readable description of the remediation action next to each approval link so the operator understands what will be executed.
5. THE Notification_Lambda SHALL include the token expiry date in the email next to each approval link.

### Requirement 3: HTML Email via SES

**User Story:** As an operator, I want remediation notification emails to be HTML-formatted with clickable buttons, so that I can easily approve actions.

#### Acceptance Criteria

1. WHEN the notification contains approval links, THE SES_Notifier SHALL send the email as HTML with clickable approval buttons.
2. THE SES_Notifier SHALL include a plain-text fallback body containing the approval URLs as raw links for email clients that do not render HTML.
3. WHEN the notification does not contain approval links, THE Notification_Lambda SHALL continue to use the existing SNS plain-text publishing path.
4. THE SES_Notifier SHALL use a verified SES identity (domain or email address) configured via an environment variable.

### Requirement 4: Approval API Endpoint

**User Story:** As an operator, I want to click the approval link and have the system validate my authorization before executing remediation, so that only legitimate approvals trigger actions.

#### Acceptance Criteria

1. THE Approval_Service SHALL expose an HTTPS GET endpoint at `/approve` that accepts a `token` query parameter.
2. WHEN the Approval_Service receives a request with a valid, non-expired, `pending` Approval_Token, THE Approval_Service SHALL update the Approval_Status to `approved` and initiate remediation execution.
3. IF the Approval_Service receives a request with an expired Approval_Token, THEN THE Approval_Service SHALL return an HTTP 410 Gone response with a message indicating the token has expired.
4. IF the Approval_Service receives a request with an Approval_Token that has already been used (Approval_Status is not `pending`), THEN THE Approval_Service SHALL return an HTTP 409 Conflict response with a message indicating the action has already been processed.
5. IF the Approval_Service receives a request with an Approval_Token that does not exist in the Approval_Store, THEN THE Approval_Service SHALL return an HTTP 404 Not Found response.
6. THE Approval_Service SHALL use a DynamoDB conditional update to atomically transition the Approval_Status from `pending` to `approved`, preventing race conditions from concurrent approval clicks.

### Requirement 5: Remediation Execution

**User Story:** As an operator, I want the system to execute the approved remediation using the agent's MCP tools, so that the confirmed fix is applied automatically after my approval.

#### Acceptance Criteria

1. WHEN the Approval_Status transitions to `approved`, THE Approval_Service SHALL invoke the Classifier_Agent with a remediation execution payload containing the Remediation_Payload from the Approval_Store.
2. THE Classifier_Agent SHALL use the appropriate MCP tools (e.g., `eks-mcp:CallPrivilegedTool` for EKS cluster upgrades) to execute the remediation actions specified in the Remediation_Payload.
3. WHEN the Classifier_Agent completes remediation successfully, THE Approval_Service SHALL update the Approval_Status to `executed` and store the execution result.
4. IF the Classifier_Agent fails to execute remediation, THEN THE Approval_Service SHALL update the Approval_Status to `failed` and store the error details.
5. THE Approval_Service SHALL set a timeout of 900 seconds for the remediation execution invocation.

### Requirement 6: Confirmation Email

**User Story:** As an operator, I want to receive a confirmation email after remediation is executed, so that I know whether the action succeeded or failed.

#### Acceptance Criteria

1. WHEN the Approval_Status transitions to `executed`, THE Approval_Service SHALL send a Confirmation_Email reporting successful remediation with a summary of actions taken.
2. WHEN the Approval_Status transitions to `failed`, THE Approval_Service SHALL send a Confirmation_Email reporting the failure with error details and suggested manual steps.
3. THE Confirmation_Email SHALL include the original notification context (event ARN, affected service, affected accounts) so the operator can correlate the confirmation with the original alert.
4. THE Approval_Service SHALL send the Confirmation_Email to the same recipient(s) as the original notification email.

### Requirement 7: Token Expiry and Cleanup

**User Story:** As a system administrator, I want expired approval tokens to be automatically cleaned up, so that the Approval_Store does not grow unbounded.

#### Acceptance Criteria

1. THE Approval_Store SHALL use a DynamoDB TTL attribute set to the token expiry timestamp to automatically delete expired records.
2. WHEN the Approval_Service receives a request for a token that has passed its expiry timestamp but has not yet been deleted by TTL, THE Approval_Service SHALL treat the token as expired and return an HTTP 410 Gone response.

### Requirement 8: Security Controls

**User Story:** As a security engineer, I want the approval mechanism to be protected against unauthorized use, so that only intended recipients can trigger remediation.

#### Acceptance Criteria

1. THE Approval_Service API Gateway SHALL enforce HTTPS for all requests.
2. THE Approval_Token SHALL contain sufficient entropy (at least 256 bits) to prevent brute-force guessing.
3. THE Approval_Service SHALL log every approval attempt (successful and failed) including the token identifier, source IP, timestamp, and outcome to CloudWatch Logs.
4. THE Approval_Service Lambda SHALL have an IAM execution role scoped to only the permissions required: DynamoDB read/write on the Approval_Store table, AgentCore InvokeAgentRuntime, SES SendEmail, and CloudWatch Logs.
5. THE Approval_Store SHALL encrypt data at rest using AWS-managed encryption.

### Requirement 9: Infrastructure as Code

**User Story:** As a DevOps engineer, I want all new infrastructure components defined in the SAM template, so that the approval system is deployed consistently and reproducibly.

#### Acceptance Criteria

1. THE SAM template SHALL define the Approval_Store DynamoDB table with partition key `token`, a TTL attribute `expires_at`, and on-demand billing.
2. THE SAM template SHALL define the Approval_Service API Gateway as an HTTP API with a single GET `/approve` route.
3. THE SAM template SHALL define the Approval_Service Lambda function with the required environment variables: Approval_Store table name, AgentCore Runtime endpoint ARN, SES sender identity, and notification recipient email.
4. THE SAM template SHALL define an SES email identity resource or accept a pre-verified identity ARN as a parameter.
5. THE SAM template SHALL grant the Notification_Lambda permissions to write to the Approval_Store and send emails via SES.

### Requirement 10: Agent Remediation Prompt Handling

**User Story:** As a developer, I want the Classifier_Agent to distinguish between classification requests and remediation execution requests, so that the agent can handle both workflows.

#### Acceptance Criteria

1. WHEN the Classifier_Agent receives a payload with a `remediation_action` key, THE Classifier_Agent SHALL execute the specified remediation using MCP tools instead of performing classification.
2. THE Classifier_Agent SHALL return a JSON result with `status` ("success" or "error"), `actions_taken` (list of executed commands), and `error` (if applicable) after remediation execution.
3. IF the Classifier_Agent cannot find the required MCP tools for the specified remediation, THEN THE Classifier_Agent SHALL return an error result indicating which tools are unavailable.
