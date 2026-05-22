# Requirements Document

## Introduction

When the PHD Notification Classifier agent confirms that an AWS Health notification has real impact on resources, the system should automatically create a Jira ticket containing the classification details, affected resources, impact analysis, and suggested remediation steps. The ticket should be assigned to the appropriate team based on the affected service or organizational unit. This integrates into the existing post-classification flow in the Lambda handler, alongside the current SES approval email path.

## Glossary

- **Lambda_Handler**: The `aha_eventbridge_lambda` Lambda function that receives classified agent output and routes it to SES, SNS, or Jira.
- **Classification_Result**: The structured JSON object returned by the agent containing notifications, impact analysis, remediation steps, and metadata.
- **Jira_Client**: A module responsible for authenticating with the Jira REST API and creating issues.
- **Ticket_Mapper**: A module responsible for transforming a Classification_Result into Jira issue fields (summary, description, priority, labels, assignee).
- **Team_Router**: A component that determines the appropriate Jira assignee or component based on the affected AWS service or organizational unit.
- **Jira_Configuration**: A set of parameters (base URL, project key, issue type, field mappings, team routing rules) stored in environment variables or AWS Secrets Manager.
- **Confirmed_Impact_Notification**: A notification within the Classification_Result where `impact_analysis.impact_status` equals `"confirmed"` and `impact_analysis.suggested_next_steps` is non-empty.

## Requirements

### Requirement 1: Create Jira Ticket on Confirmed Impact

**User Story:** As an operations engineer, I want a Jira ticket to be automatically created when the agent confirms impact with remediation steps, so that the team can track and act on the issue without manual ticket creation.

#### Acceptance Criteria

1. WHEN the Lambda_Handler receives a Classification_Result containing at least one Confirmed_Impact_Notification, THE Lambda_Handler SHALL invoke the Jira_Client to create one Jira ticket per Confirmed_Impact_Notification.
2. IF the Jira_Client fails to create a ticket, THEN THE Lambda_Handler SHALL fall back to the existing SNS plain-text notification path, publishing the summary to the SNS topic so the operator still receives the notification.
3. WHEN a Jira ticket is created successfully, THE Lambda_Handler SHALL log the Jira issue key, include the issue key in the SES approval email body, and also publish a summary to SNS with the Jira issue key referenced.

### Requirement 2: Jira Ticket Content Mapping

**User Story:** As an operations engineer, I want the Jira ticket to contain all relevant classification details, so that I have full context without needing to look up the original notification.

#### Acceptance Criteria

1. THE Ticket_Mapper SHALL set the Jira issue summary to a string containing the classification category and the affected service name, truncated to 255 characters.
2. THE Ticket_Mapper SHALL set the Jira issue description to a formatted text block containing: the event ARN, classification category, classification reason, affected accounts with environment type, affected resources, impact analysis summary, risk level, and the full list of suggested remediation steps.
3. THE Ticket_Mapper SHALL set the Jira issue priority based on the risk_level field: "high" maps to Jira priority "Highest", "medium" maps to "High", and "low" maps to "Medium".
4. THE Ticket_Mapper SHALL add Jira labels including the classification category, the affected service name, and the string "phd-auto-created".
5. WHEN the Classification_Result contains a cost_projection for the notification, THE Ticket_Mapper SHALL include the cost projection details in the Jira issue description.

### Requirement 3: Team-Based Ticket Assignment

**User Story:** As a team lead, I want Jira tickets to be routed to the correct team based on the affected service or organizational unit, so that the right people are notified immediately.

#### Acceptance Criteria

1. THE Team_Router SHALL determine the Jira assignee or component by matching the affected service name against a configurable service-to-team mapping stored in the Jira_Configuration.
2. WHEN the affected service does not match any entry in the service-to-team mapping, THE Team_Router SHALL fall back to a configurable default assignee or component from the Jira_Configuration.
3. WHERE the Classification_Result contains organizational unit information for affected accounts, THE Team_Router SHALL use the OU-to-team mapping from the Jira_Configuration as a secondary routing rule when the service-based mapping produces no match.

### Requirement 4: Jira API Authentication

**User Story:** As a platform engineer, I want Jira API credentials to be securely stored and retrieved, so that the integration does not expose sensitive tokens.

#### Acceptance Criteria

1. THE Jira_Client SHALL retrieve the Jira API token from AWS Secrets Manager using a secret ARN provided in the Jira_Configuration.
2. THE Jira_Client SHALL authenticate with the Jira REST API using Basic Authentication with the configured Jira user email and the retrieved API token.
3. IF the Jira_Client fails to retrieve the secret from Secrets Manager, THEN THE Jira_Client SHALL raise an error that the Lambda_Handler logs, and the Lambda_Handler SHALL continue with the existing notification flow.

### Requirement 5: Configurable Jira Settings

**User Story:** As a platform engineer, I want Jira project, issue type, and field mappings to be configurable without code changes, so that the integration can be adapted to different Jira setups.

#### Acceptance Criteria

1. THE Lambda_Handler SHALL read the following Jira_Configuration values from environment variables: Jira base URL, Jira project key, Jira issue type name, Jira user email, Secrets Manager secret ARN for the API token, and default assignee.
2. THE Lambda_Handler SHALL read the service-to-team mapping and OU-to-team mapping from a JSON-formatted environment variable or from a JSON object stored in the same Secrets Manager secret.
3. IF any required Jira_Configuration value is missing, THEN THE Lambda_Handler SHALL skip Jira ticket creation, log a warning, and fall back to the existing SNS plain-text notification path.

### Requirement 6: Jira Ticket Formatting

**User Story:** As an operations engineer, I want the Jira ticket description to be well-formatted and readable, so that I can quickly understand the issue and take action.

#### Acceptance Criteria

1. THE Ticket_Mapper SHALL format the Jira issue description using Jira wiki markup or Atlassian Document Format, with separate sections for: Event Details, Classification, Affected Accounts, Impact Analysis, and Remediation Steps.
2. THE Ticket_Mapper SHALL render each remediation step as a numbered list item in the Remediation Steps section.
3. THE Ticket_Mapper SHALL render affected accounts as a table with columns: Account ID, Account Name, Environment Type, and Affected Resources.
4. FOR ALL valid Classification_Result objects, formatting then parsing the Jira description SHALL produce an equivalent structured representation (round-trip property).

### Requirement 7: Duplicate Ticket Prevention

**User Story:** As an operations engineer, I want the system to avoid creating duplicate Jira tickets for the same health event, so that the backlog stays clean.

#### Acceptance Criteria

1. WHEN creating a Jira ticket, THE Jira_Client SHALL search for existing open tickets in the configured project with a matching event ARN label before creating a new ticket.
2. WHEN an existing open ticket with the same event ARN label is found, THE Jira_Client SHALL skip ticket creation and log the existing issue key.
3. IF the duplicate check search fails, THEN THE Jira_Client SHALL proceed with ticket creation and log a warning about the failed duplicate check.

### Requirement 8: Infrastructure Configuration

**User Story:** As a platform engineer, I want the SAM template to include the necessary IAM permissions and environment variables for Jira integration, so that deployment is automated.

#### Acceptance Criteria

1. THE SAM template SHALL include an IAM policy statement granting the Lambda_Handler permission to call `secretsmanager:GetSecretValue` on the configured Jira secret ARN.
2. THE SAM template SHALL include parameters for all Jira_Configuration values with sensible defaults and descriptions.
3. THE SAM template SHALL pass all Jira_Configuration parameters as environment variables to the Lambda_Handler function.
