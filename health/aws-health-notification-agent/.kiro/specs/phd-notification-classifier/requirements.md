# Requirements Document

## Introduction

This feature implements an agent running on the AWS AgentCore runtime that consolidates and classifies AWS Personal Health Dashboard (PHD) notifications across the organization. The agent receives AWS Health event payloads from the aha-eventbridge-lambda Lambda function via the `@app.entrypoint` decorator — it does not fetch health events directly. For each received health event, the agent enriches the event with account context from AWS Organizations (account name, OU membership, and account tags) before performing classification or analysis. The agent categorizes notifications into six categories — Service Disruption, Breaking Changes, Security-Related, Cost Implications, Informational, and Unclassified — with a defined priority ordering. Each notification also receives an urgency level (critical/high/medium/low) based on deadline proximity, and the agent extracts action deadlines from notification text. The agent provides automated impact analysis across production and non-production environments, including EKS cluster verification and upgrade capabilities via boto3 tools. After classification and impact analysis, the agent publishes a structured notification summary to a configured SNS topic for downstream consumption. The system prompt can be loaded from S3 for updates without container rebuild, and the Bedrock model ID is configurable via environment variable.

## Glossary

- **Agent**: The autonomous software component running on the AgentCore_Runtime that receives health event payloads, enriches them with account context, performs consolidation, classification, impact analysis, and integration with external systems.
- **AgentCore_Runtime**: The AWS AgentCore execution environment that hosts and manages the Agent lifecycle.
- **aha-eventbridge-lambda**: The AWS Lambda function that serves as a glue layer between Amazon EventBridge and the Agent. It receives AWS Health events forwarded by AHA via EventBridge and invokes the AgentCore_Runtime endpoint, passing the health event payload to the Agent via the `@app.entrypoint` decorator.
- **AHA**: AWS Health Aware (https://github.com/aws-samples/aws-health-aware), the open-source tool that forwards AWS Health events to EventBridge. The aha-eventbridge-lambda Lambda function receives these forwarded events and invokes the Agent.
- **PHD_API**: The AWS Health API used to retrieve organizational health events and notifications.
- **Notification**: An unclosed event received by the Agent as a health event payload from the aha-eventbridge-lambda Lambda function.
- **Account_Context**: The enrichment data retrieved from AWS Organizations for each affected account, including the account name, OU membership, and account tags. Used to determine Environment_Type.
- **get_account_context**: The Agent tool that calls the AWS Organizations API to retrieve Account_Context for affected accounts.
- **get_account_application_trust_store**: The Agent tool that retrieves application-level context (e.g., trust store information) for affected accounts when the notification's impact depends on application configuration. Reads from DynamoDB (`APP_CONTEXT_TABLE_NAME`) or static JSON (`APP_CONTEXT_JSON`), returning empty context if neither is configured.
- **Application_Context**: Application-level configuration data for an affected account, such as TLS trust store contents, certificate pinning settings, or other application-specific configuration that determines whether a notification's impact is confirmed or unconfirmed. Sourced from DynamoDB or static JSON configuration.
- **OU**: Organizational Unit — a logical grouping of AWS accounts within AWS Organizations, used to determine account classification and Environment_Type.
- **Consolidated_View**: A unified representation that groups related Notifications across accounts into a single summary with account-level and organization-wide visibility.
- **Classification**: The category assigned to a Notification. One of SERVICE_DISRUPTION, BREAKING_CHANGE, SECURITY_RELATED, COST_IMPLICATION, INFORMATIONAL, or UNCLASSIFIED.
- **Classification_Priority**: The evaluation order for classification: SERVICE_DISRUPTION > BREAKING_CHANGE > SECURITY_RELATED > COST_IMPLICATION > INFORMATIONAL > UNCLASSIFIED. Each notification receives exactly one classification based on the highest-priority matching category.
- **Classification_Reason**: A human-readable text explanation describing why a specific Classification was assigned to a Notification.
- **Service_Disruption**: A PHD event indicating an active outage, regional disruption, or ongoing service degradation that is currently impacting workloads.
- **Breaking_Change**: A PHD event that indicates a service deprecation, API removal, endpoint retirement, or any change that will cause existing workloads to stop functioning without customer action.
- **Security_Related**: A PHD event involving security vulnerabilities, compliance issues, or security patches requiring attention.
- **Cost_Implication**: A PHD event indicating financial impact such as extended support fees, pricing changes, or resource cost increases (e.g., end-of-standard-support for RDS, Lambda runtimes, EKS versions).
- **Informational**: A PHD event that requires no action — renewals, credits, resolved events, or general advisories.
- **Unclassified**: A PHD event with insufficient detail to assign a meaningful classification.
- **Urgency**: A per-notification field (critical/high/medium/low) indicating how urgently action is needed, determined by deadline proximity.
- **Deadline**: An action deadline extracted from the notification text (e.g., "before March 31, 2025"). The agent parses dates from the event description to populate this field.
- **EKS_Cluster_Tools**: Local boto3 `@tool` functions (`describe_eks_cluster`, `upgrade_eks_cluster`) that verify EKS cluster versions and initiate upgrades. These are NOT MCP tools — they use boto3 directly.
- **S3_System_Prompt**: The system prompt loaded from S3 (`SYSTEM_PROMPT_S3_BUCKET`/`SYSTEM_PROMPT_S3_KEY`) at cold start, enabling prompt updates without container rebuild. Falls back to the embedded default if S3 is not configured or read fails.
- **BEDROCK_MODEL_ID**: Environment variable controlling which Bedrock model the agent uses (e.g., `eu.anthropic.claude-sonnet-4-6`). Configurable per deployment.
- **Classifier**: The component within the Agent responsible for evaluating a Notification and assigning a Classification and Classification_Reason.
- **Impact_Analyzer**: The component within the Agent responsible for assessing the impact of a Notification across affected accounts and environments.
- **Cost_Estimator**: The component within the Agent responsible for projecting financial impact of Cost_Implication notifications.
- **SNS_Notifier**: The component within the Agent responsible for publishing structured notification summaries to the configured SNS_Topic after classification and impact analysis.
- **SNS_Topic**: The Amazon SNS topic (identified by the SNS_TOPIC_ARN environment variable) to which the Agent publishes notification summaries for downstream consumption.
- **Environment_Type**: The classification of an AWS account as either production or non-production, used for risk assessment.
- **Impact_Status**: A field in the impact analysis indicating whether the impact to resources is "confirmed" (the Agent has sufficient information to definitively determine resources are impacted) or "unconfirmed" (the Agent cannot confirm whether resources are actually impacted based on available information).
- **Suggested_Verification_Steps**: A list of specific, actionable steps the operator should take to verify the actual impact when the Impact_Status is "unconfirmed". The steps are tailored to the Notification type and affected service.

## Requirements

### Requirement 1: Agent Lifecycle on AgentCore Runtime

**User Story:** As a platform operator, I want the agent to run on the AgentCore runtime, so that it integrates with our existing agent management infrastructure.

#### Acceptance Criteria

1. THE Agent SHALL execute within the AgentCore_Runtime environment.
2. WHEN the AgentCore_Runtime invokes the Agent via the `@app.entrypoint` decorator, THE Agent SHALL accept the health event payload from the aha-eventbridge-lambda Lambda function.
3. WHEN the Agent streams the Strands Agent response, THE Agent `@app.entrypoint` async generator SHALL yield only the final result text from the "result" streaming event, not the full streaming trace. This keeps the response size manageable for downstream consumers.
4. IF the Agent receives a malformed or unparseable health event payload, THEN THE Agent SHALL return an error response describing the payload validation failure.

### Requirement 2: Health Event Ingestion via aha-eventbridge-lambda

**User Story:** As a platform operator, I want the agent to receive health event payloads from the aha-eventbridge-lambda Lambda function, so that I can build on a proven and maintained event-forwarding pipeline.

#### Acceptance Criteria

1. WHEN the aha-eventbridge-lambda Lambda function invokes the AgentCore_Runtime endpoint, THE Agent SHALL receive and parse the health event payload delivered via the `@app.entrypoint` decorator.
2. THE Agent SHALL accept and process all unclosed Notifications received as health event payloads from the aha-eventbridge-lambda Lambda function.
3. THE Agent SHALL filter received events to include only those with an open or upcoming status.
4. IF the aha-eventbridge-lambda delivers a health event payload with multiple affected accounts, THEN THE Agent SHALL process all affected accounts before proceeding to classification.
5. IF the Agent receives an empty or invalid health event payload, THEN THE Agent SHALL return an error response describing the ingestion failure.
6. THE Agent SHALL support an optional `limit` parameter that caps the number of Notifications processed. WHEN `limit` is set to a positive integer, THE Agent SHALL process at most that many Notifications. WHEN `limit` is 0 or omitted, THE Agent SHALL process all Notifications.

### Requirement 3: Notification Consolidation

**User Story:** As a platform operator, I want multiple related AWS Health notifications consolidated into a single unified view, so that I can understand organization-wide impact without reviewing each notification individually.

#### Acceptance Criteria

1. WHEN multiple Notifications relate to the same health event across different accounts, THE Agent SHALL group those Notifications into a single Consolidated_View.
2. THE Consolidated_View SHALL include account-level detail for each affected account.
3. THE Consolidated_View SHALL include an organization-wide impact summary.
4. THE Consolidated_View SHALL categorize impact separately for production and non-production environments.
5. WHEN a new Notification arrives that relates to an existing Consolidated_View, THE Agent SHALL update the existing Consolidated_View rather than create a new one.

### Requirement 4: Classify Notifications as Service Disruptions

**User Story:** As a platform operator, I want active outages and regional disruptions classified as Service Disruptions, so that my team can respond immediately to ongoing incidents.

#### Acceptance Criteria

1. WHEN a Notification describes an active outage, regional disruption, or ongoing service degradation that is currently impacting workloads, THE Classifier SHALL assign a Classification of SERVICE_DISRUPTION to that Notification.
2. WHEN a Notification indicates a service is currently unavailable or degraded (not a future change), THE Classifier SHALL assign a Classification of SERVICE_DISRUPTION.

### Requirement 5: Classify Notifications as Breaking Changes

**User Story:** As a platform operator, I want service changes requiring customer action classified as Breaking Changes, so that my team can prioritize urgent remediation work.

#### Acceptance Criteria

1. WHEN a Notification describes a Breaking_Change, THE Classifier SHALL assign a Classification of BREAKING_CHANGE to that Notification.
2. WHEN a Notification indicates a service deprecation, API removal, or endpoint retirement that will cause existing workloads to stop functioning without customer action, THE Classifier SHALL assign a Classification of BREAKING_CHANGE.

### Requirement 6: Classify Notifications as Security-Related

**User Story:** As a platform operator, I want security vulnerabilities, compliance issues, and security patches classified as Security-Related, so that my security team can respond promptly.

#### Acceptance Criteria

1. WHEN a Notification describes a Security_Related event, THE Classifier SHALL assign a Classification of SECURITY_RELATED to that Notification.
2. WHEN a Notification indicates a security vulnerability, compliance issue, or security patch, THE Classifier SHALL assign a Classification of SECURITY_RELATED.

### Requirement 7: Classify Notifications as Cost Implications

### Requirement 7: Classify Notifications as Cost Implications

**User Story:** As a platform operator, I want events with financial impact classified as Cost Implications, so that my team can plan and act before additional charges apply.

#### Acceptance Criteria

1. WHEN a Notification describes a Cost_Implication, THE Classifier SHALL assign a Classification of COST_IMPLICATION to that Notification.
2. WHEN a Notification indicates extended support fees, pricing changes, or resource cost increases, THE Classifier SHALL assign a Classification of COST_IMPLICATION.

### Requirement 8: Classify Notifications as Informational

**User Story:** As a platform operator, I want notifications that require no action classified as Informational, so that my team can deprioritize them.

#### Acceptance Criteria

1. WHEN a Notification requires no customer action (renewals, credits, resolved events, general advisories), THE Classifier SHALL assign a Classification of INFORMATIONAL to that Notification.
2. WHEN a Notification does not match SERVICE_DISRUPTION, BREAKING_CHANGE, SECURITY_RELATED, or COST_IMPLICATION criteria, and the notification content is clear enough to determine no action is needed, THE Classifier SHALL assign a Classification of INFORMATIONAL.

### Requirement 9: Classify Notifications as Unclassified

**User Story:** As a platform operator, I want notifications with insufficient detail flagged as Unclassified, so that my team knows which events need manual review.

#### Acceptance Criteria

1. WHEN a Notification has insufficient detail to assign a meaningful classification, THE Classifier SHALL assign a Classification of UNCLASSIFIED to that Notification.
2. WHEN a Notification does not match any of the five defined categories (SERVICE_DISRUPTION, BREAKING_CHANGE, SECURITY_RELATED, COST_IMPLICATION, INFORMATIONAL), THE Classifier SHALL assign a Classification of UNCLASSIFIED.

### Requirement 10: Provide Classification Reasoning

**User Story:** As a platform operator, I want each classification to include an explanation, so that I can understand and validate the agent's decision.

#### Acceptance Criteria

1. THE Classifier SHALL produce a Classification_Reason for every classified Notification.
2. THE Classification_Reason SHALL reference the specific attributes of the Notification that led to the assigned Classification.
3. THE Classification_Reason SHALL be a human-readable text string of at least one sentence.

### Requirement 11: Mutually Exclusive Classification with Priority Ordering

**User Story:** As a platform operator, I want each notification to receive exactly one classification with a defined priority, so that there is no ambiguity in categorization.

#### Acceptance Criteria

1. THE Classifier SHALL assign exactly one Classification to each Notification.
2. THE Classifier SHALL evaluate categories in priority order: SERVICE_DISRUPTION > BREAKING_CHANGE > SECURITY_RELATED > COST_IMPLICATION > INFORMATIONAL > UNCLASSIFIED. Each notification receives the highest-priority matching classification.

### Requirement 12: Urgency and Deadline Extraction

**User Story:** As a platform operator, I want each notification to include an urgency level and action deadline, so that my team can prioritize response based on time sensitivity.

#### Acceptance Criteria

1. THE Agent SHALL assign an urgency level (critical, high, medium, or low) to each classified Notification based on deadline proximity.
2. THE Agent SHALL extract action deadlines from the notification text when present (e.g., "before March 31, 2025", "by end of Q1 2025").
3. WHEN a deadline is within 7 days, THE Agent SHALL assign urgency "critical". WHEN within 30 days, "high". WHEN within 90 days, "medium". WHEN beyond 90 days or no deadline, "low".
4. THE output for each notification SHALL include an `urgency` field and a `deadline` field (null if no deadline is extractable).

### Requirement 13: Breaking Change Assessment

**User Story:** As a platform operator, I want automated inspection of breaking changes to determine if action is required in my environments, so that I can focus remediation efforts where they matter.

#### Acceptance Criteria

1. WHEN a Notification is classified as BREAKING_CHANGE, THE Impact_Analyzer SHALL inspect all affected accounts and resources to determine if customer action is required.
2. THE Impact_Analyzer SHALL assess risk based on Environment_Type, assigning higher risk to production environments than non-production environments.
3. THE Impact_Analyzer SHALL produce an impact summary listing each affected account, the affected resources, and the required action. THE Impact_Analyzer SHALL preserve the full event description in the impact summary without truncation.
4. IF no affected resources are found in any account, THEN THE Impact_Analyzer SHALL indicate that no action is required for that Notification.
5. THE Impact_Analyzer SHALL include an impact_status field in the impact analysis with a value of "confirmed" or "unconfirmed".
6. WHEN the Agent has sufficient information from tools, Account_Context, or the Notification itself to definitively determine that resources are impacted, THE Impact_Analyzer SHALL set the impact_status to "confirmed". Also, suggest remediation steps to address the issue. 
7. WHEN the Agent cannot definitively determine whether resources are impacted based on available information, THE Impact_Analyzer SHALL set the impact_status to "unconfirmed". Also, suggest steps to find out the actual impact.
8. WHEN the impact_status is "unconfirmed", THE Impact_Analyzer SHALL include a list of suggested_verification_steps containing specific, actionable steps the operator should take to verify the actual impact.
9. THE suggested_verification_steps SHALL be specific to the Notification type and affected service, not generic.

### Requirement 14: Cost Impact Estimation

**User Story:** As a platform operator, I want automated cost projections for notifications with financial impact, so that I can make informed budgeting decisions.

#### Acceptance Criteria

1. WHEN a Notification is classified as COST_IMPLICATION, THE Cost_Estimator SHALL produce a projected cost impact for each affected account.
2. THE Cost_Estimator SHALL aggregate projected costs across all affected accounts into an organization-wide total.
3. THE Cost_Estimator SHALL track historical cost data for similar events to improve projection accuracy.
4. IF the Cost_Estimator cannot determine a cost projection, THEN THE Cost_Estimator SHALL indicate that the cost impact is unknown and provide the reason.

### Requirement 15: Classification Output Format

**User Story:** As a platform operator, I want the classification results returned in a structured format, so that downstream systems can consume them programmatically.

#### Acceptance Criteria

1. THE Agent SHALL return classification results as a JSON array.
2. WHEN a Notification is classified, THE Agent SHALL include the Notification identifier, Classification, Classification_Reason, urgency, deadline, and affected accounts in the output.
3. THE Agent SHALL include the original Notification event type, affected service, and Environment_Type impact breakdown in the output for each classified Notification.
4. WHEN a Notification has a cost projection, THE Agent SHALL include the projected cost impact in the output.
5. WHEN a Notification has a breaking change assessment, THE Agent SHALL include the impact analysis and required actions in the output.
6. THE output SHALL include counts for all six categories: `service_disruption_count`, `breaking_change_count`, `security_related_count`, `cost_implication_count`, `informational_count`, and `unclassified_count`.

### Requirement 16: SNS Topic Notification

**User Story:** As a platform operator, I want the agent to publish notification summaries to an SNS topic after classification and impact analysis, so that downstream systems can subscribe and take action independently.

#### Acceptance Criteria

1. WHEN the Agent completes classification and impact analysis for a Notification, THE SNS_Notifier SHALL publish a notification summary to the configured SNS_Topic.
2. THE SNS_Notifier SHALL include the Classification, Classification_Reason, impact analysis, cost projections, and affected accounts as a structured JSON payload in the SNS message.
3. THE SNS_Notifier SHALL read the SNS topic ARN from the SNS_TOPIC_ARN environment variable.
4. IF the SNS_TOPIC_ARN environment variable is not set, THEN THE Agent SHALL log a warning and skip SNS notification.
5. IF the SNS_Notifier fails to publish to the SNS_Topic, THEN THE SNS_Notifier SHALL log the failure and include the failure details in the Agent output.
6. THE SNS_Notifier SHALL include the Notification identifier, event type, and affected service in the SNS message payload.
7. THE SNS_Notifier SHALL extract the AWS region from the SNS topic ARN and use it when creating the SNS client, rather than relying on a default region configuration.

### Requirement 17: Account Context Enrichment via get_account_context

**User Story:** As a platform operator, I want the agent to enrich each health event with account context from AWS Organizations before classification, so that the agent can accurately determine environment type and provide account-aware impact analysis.

#### Acceptance Criteria

1. WHEN the Agent receives a health event payload, THE Agent SHALL call the get_account_context tool for all affected accounts before performing classification or analysis.
2. THE get_account_context tool SHALL call the AWS Organizations API (describe_account, list_parents, list_tags_for_resource) to retrieve the account name, OU membership, and account tags for a given account.
3. THE Agent SHALL enrich the health event with the retrieved Account_Context for each affected account before proceeding to classification.
4. THE Agent SHALL use the Account_Context to determine Environment_Type (production or non-production) based on account tags or OU membership.
5. IF the AWS Organizations API call fails for an account, THEN THE Agent SHALL log the failure and default the Environment_Type to "unknown" for that account.
6. THE get_account_context tool SHALL return the account name, OU membership path, and a dictionary of account tags for each queried account.

### Requirement 18: AgentCore Runtime IAM Permissions

**User Story:** As a platform operator, I want the AgentCore runtime role to have all necessary IAM permissions, so that the agent's tools can access AWS services without access denied errors.

#### Acceptance Criteria

1. THE AgentCore_Runtime IAM role SHALL have permission to call `organizations:DescribeAccount`, `organizations:ListParents`, `organizations:ListTagsForResource`, and `organizations:DescribeOrganizationalUnit` for the get_account_context tool.
2. THE AgentCore_Runtime IAM role SHALL have permission to call `sns:Publish` on the configured SNS_Topic for the publish_to_sns tool.
3. THE AgentCore_Runtime IAM role SHALL have permission to call `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` for the Bedrock model used by the Strands Agent. The Bedrock model ID SHALL use the regional prefix appropriate for the deployment region (e.g., `eu.anthropic.claude-sonnet-4-20250514-v1:0` for EU regions).
4. THE AgentCore_Runtime IAM role SHALL have permission to pull the container image from the ECR repository.
5. THE Agent SHALL specify `region_name` explicitly when creating boto3 clients, since the AgentCore container may not have `AWS_DEFAULT_REGION` set.

### Requirement 19: Application Context from DynamoDB/JSON

**User Story:** As a platform operator, I want the agent to retrieve application-level context from a configurable data source (DynamoDB or static JSON), so that the agent can determine whether notification impact is confirmed or unconfirmed without hardcoded data.

#### Acceptance Criteria

1. WHEN a Notification's impact depends on application-level configuration (e.g., TLS trust stores, certificate pinning), THE Agent SHALL call the get_account_application_trust_store tool for each affected account before determining the impact_status.
2. THE get_account_application_trust_store tool SHALL attempt to read from DynamoDB first (using the `APP_CONTEXT_TABLE_NAME` environment variable), then fall back to static JSON (using the `APP_CONTEXT_JSON` environment variable).
3. THE Agent SHALL compare the Application_Context returned by get_account_application_trust_store against the Notification's impact criteria to determine whether the impact_status is "confirmed" or "unconfirmed".
4. WHEN get_account_application_trust_store returns an empty trust_store or neither data source is configured, THE Agent SHALL set the impact_status to "unconfirmed".
5. THE get_account_application_trust_store tool SHALL return a dict containing the account_id, trust_store (list of CAs), and applications (list of application details with trust store and deployment info).

### Requirement 20: EKS Cluster Tools

**User Story:** As a platform operator, I want the agent to verify EKS cluster versions and initiate upgrades, so that breaking changes affecting EKS can be assessed and remediated.

#### Acceptance Criteria

1. THE Agent SHALL include `describe_eks_cluster` and `upgrade_eks_cluster` as local `@tool` functions using boto3 (not MCP).
2. THE `describe_eks_cluster` tool SHALL call the EKS API to retrieve cluster version, status, and configuration for a given cluster name.
3. THE `upgrade_eks_cluster` tool SHALL initiate an EKS cluster upgrade to a specified target version.
4. BOTH tools SHALL pass `region_name` explicitly to the boto3 client.
5. THE `upgrade_eks_cluster` tool SHALL only be invoked during the remediation flow (after human approval), never during classification.

### Requirement 21: S3-Backed System Prompt

**User Story:** As a platform operator, I want the system prompt loadable from S3, so that I can update classification behavior without rebuilding the container.

#### Acceptance Criteria

1. THE Agent SHALL attempt to load the system prompt from S3 at cold start using the `SYSTEM_PROMPT_S3_BUCKET` and `SYSTEM_PROMPT_S3_KEY` environment variables.
2. IF S3 is not configured (bucket env var not set) or the S3 read fails, THE Agent SHALL fall back to the embedded default system prompt.
3. THE Agent SHALL log whether the prompt was loaded from S3 or the embedded default.

### Requirement 22: Configurable Model ID

**User Story:** As a platform operator, I want the Bedrock model ID configurable via environment variable, so that I can switch models without code changes.

#### Acceptance Criteria

1. THE Agent SHALL read the Bedrock model ID from the `BEDROCK_MODEL_ID` environment variable.
2. IF `BEDROCK_MODEL_ID` is not set, THE Agent SHALL use a default model ID (e.g., `eu.anthropic.claude-sonnet-4-6`).
3. THE model ID SHALL use the regional prefix appropriate for the deployment region.
