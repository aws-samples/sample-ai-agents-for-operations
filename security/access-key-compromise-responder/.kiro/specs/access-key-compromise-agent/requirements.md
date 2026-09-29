# Requirements Document

## Introduction

This document captures the requirements for the AWS Access Key Compromise Response Agent — a two-agent system built on Amazon Bedrock AgentCore that automatically detects, investigates, and remediates compromised AWS access keys. When a key is exposed on a public repository or actively used by a threat actor, the system responds in under 5 seconds by analyzing CloudTrail activity, discovering created resources, quarantining them, and sending an investigation report.

## Glossary

- **Investigation_Agent**: The read-only Amazon Bedrock AgentCore agent responsible for detecting compromised keys, analyzing CloudTrail logs, discovering transitive resources, generating scoped IAM policies, and orchestrating remediation via the Remediation Agent.
- **Remediation_Agent**: The write-capable Amazon Bedrock AgentCore agent that receives a scoped IAM policy and executes quarantine actions on compromised resources.
- **Compromise_Alert**: A normalized data structure representing a detected compromised access key, including the key ID, username, account ID, detection source, and severity.
- **Attack_Chain**: A tree structure representing all resources created directly or transitively by a compromised access key, with unlimited depth traversal.
- **Account_Classifier**: The component that determines whether an AWS account is production or non-production using AWS Organizations tags.
- **Paging_Engine**: The component that applies intelligent rules to determine whether the security team should be paged based on account type, severity, and business hours.
- **CloudTrail_Analyzer**: The component that queries AWS CloudTrail for access key activity, builds historical baselines, and detects behavioral anomalies.
- **Resource_Discoverer**: The component that performs transitive resource discovery by following IAM principals created by the compromised key to find all downstream resources.
- **Quarantine_Handler**: A resource-type-specific handler that isolates a compromised resource without deleting it.
- **Dynamic_Policy_Generator**: The component that generates minimal, resource-scoped IAM policies granting the Remediation Agent only the permissions needed for specific quarantine actions.
- **EventBridge_Trigger**: The AWS Lambda function that receives EventBridge events from detection sources and invokes the Investigation Agent on Amazon Bedrock AgentCore.

## Requirements

### Requirement 1: Multi-Source Compromise Detection

**User Story:** As a security engineer, I want the system to detect compromised access keys from multiple independent AWS detection sources, so that no compromise goes undetected regardless of how it was discovered.

#### Acceptance Criteria

1. WHEN an AWS Health AWS_RISK_CREDENTIALS_EXPOSED event is received, THE Investigation_Agent SHALL parse the event and extract the compromised access key ID, username, and account ID.
2. WHEN a Security Hub finding with ResourceType AwsIamAccessKey and RecordState ACTIVE is received, THE Investigation_Agent SHALL parse the finding and extract the compromised access key ID, username, and account ID.
3. WHEN a Trusted Advisor Exposed Access Keys check result with status ERROR or WARN is received, THE Investigation_Agent SHALL parse the result and extract the compromised access key ID, username, and exposure location.
4. WHEN multiple detection sources report the same access key ID, THE Investigation_Agent SHALL deduplicate alerts and process the key only once.
5. IF a detection source is unavailable due to subscription requirements, THEN THE Investigation_Agent SHALL log the unavailability and continue checking remaining sources.

### Requirement 2: Event-Driven Trigger Architecture

**User Story:** As a platform engineer, I want the system to be triggered automatically by AWS events, so that response begins immediately without human intervention.

#### Acceptance Criteria

1. THE EventBridge_Trigger SHALL subscribe to three independent EventBridge rules: AWS Health (AWS_RISK_CREDENTIALS_EXPOSED), Security Hub (AwsIamAccessKey findings), and Trusted Advisor (Exposed Access Keys check).
2. WHEN an EventBridge event is received, THE EventBridge_Trigger SHALL normalize the event into a standard alert context containing access_key_id, username, account_id, alert_source, event_type, and region.
3. WHEN the alert context is constructed, THE EventBridge_Trigger SHALL invoke the Investigation Agent on Amazon Bedrock AgentCore via the invoke_agent_runtime API.
4. IF the EventBridge_Trigger fails to process an event, THEN THE EventBridge_Trigger SHALL send the event to an SQS dead-letter queue for later retry.
5. THE EventBridge_Trigger SHALL support manual invocation with a direct payload containing access_key_id, username, and account_id.

### Requirement 3: CloudTrail Analysis with Historical Baseline

**User Story:** As a security analyst, I want the system to analyze CloudTrail logs with a historical baseline, so that I can distinguish normal activity from attacker behavior.

#### Acceptance Criteria

1. WHEN an alert is received, THE CloudTrail_Analyzer SHALL query CloudTrail for all events associated with the compromised access key within a configurable lookback period (default: 7 days).
2. THE CloudTrail_Analyzer SHALL build a historical baseline by querying CloudTrail for the compromised key's activity over a configurable baseline period (default: 90 days) prior to the compromise timestamp.
3. THE CloudTrail_Analyzer SHALL record the historical baseline including: total events, unique API calls, unique resources created, average daily API call rate, average daily resource creation rate, regions accessed, IP addresses used, active hours, and active days.
4. THE CloudTrail_Analyzer SHALL parse each CloudTrail event into a structured API call record containing: timestamp, service, action, source IP, user agent, region, error code, request parameters, and response elements.
5. THE CloudTrail_Analyzer SHALL categorize events into resource creation (40+ event types), modification, and deletion activities.

### Requirement 4: Behavioral Anomaly Detection

**User Story:** As a security analyst, I want the system to detect behavioral anomalies by comparing post-compromise activity against the historical baseline, so that I can identify attacker actions.

#### Acceptance Criteria

1. WHEN post-compromise activity includes API calls not present in the historical baseline, THE CloudTrail_Analyzer SHALL generate a HIGH severity "new_api_calls" anomaly.
2. WHEN post-compromise activity originates from IP addresses not present in the historical baseline, THE CloudTrail_Analyzer SHALL generate a HIGH severity "new_ip_addresses" anomaly.
3. WHEN post-compromise activity occurs in AWS regions not present in the historical baseline, THE CloudTrail_Analyzer SHALL generate a MEDIUM severity "new_regions" anomaly.
4. WHEN the post-compromise API call rate exceeds 3 times the historical daily average, THE CloudTrail_Analyzer SHALL generate a MEDIUM severity "volume_spike" anomaly.
5. WHEN post-compromise activity includes high-risk API calls (Create, Delete, Put, Attach, Detach operations), THE CloudTrail_Analyzer SHALL generate a CRITICAL severity "high_risk_operations" anomaly.
6. WHEN the post-compromise resource creation rate exceeds 2 times the historical daily average, THE CloudTrail_Analyzer SHALL generate a HIGH severity "resource_creation_spike" anomaly.

### Requirement 5: Transitive Resource Discovery

**User Story:** As a security analyst, I want the system to discover all resources created by the compromised key and any transitive resources created by those resources, so that the full attack chain is identified.

#### Acceptance Criteria

1. THE Resource_Discoverer SHALL identify all resources created directly by the compromised access key after the compromise timestamp (level 0 of the attack chain).
2. WHEN a discovered resource is an IAM principal (user or role), THE Resource_Discoverer SHALL recursively query CloudTrail for resources created by that principal.
3. THE Resource_Discoverer SHALL continue recursive discovery with unlimited depth until no new transitive resources are found or a configurable timeout (default: 10 minutes) is reached.
4. THE Resource_Discoverer SHALL support discovery of 40+ AWS resource creation event types including: EC2 instances, security groups, key pairs, volumes, snapshots, AMIs, VPCs, subnets, IAM users, roles, policies, access keys, S3 buckets, Lambda functions, RDS instances, ECS clusters, tasks, services, DynamoDB tables, SNS topics, SQS queues, CloudFormation stacks, ECR repositories, Secrets Manager secrets, SSM parameters, Glue jobs, SageMaker notebooks, and Lightsail instances.
5. THE Resource_Discoverer SHALL build an attack chain tree recording: resource ID, resource type, resource ARN, creation timestamp, creating principal, attack chain level, parent resource, and child resources.

### Requirement 6: Risk Level Calculation

**User Story:** As a security engineer, I want the system to calculate a risk level for each compromise, so that response priority can be determined.

#### Acceptance Criteria

1. THE CloudTrail_Analyzer SHALL calculate a risk level of CRITICAL, HIGH, MEDIUM, or LOW based on anomaly severity scores, attack chain depth, and resource creation activity.
2. WHEN anomalies with CRITICAL severity are detected, THE CloudTrail_Analyzer SHALL weight the risk score by 10 points per anomaly.
3. WHEN anomalies with HIGH severity are detected, THE CloudTrail_Analyzer SHALL weight the risk score by 5 points per anomaly.
4. WHEN the attack chain contains resources at depth greater than 0 (transitive resources), THE CloudTrail_Analyzer SHALL increase the risk score.
5. THE CloudTrail_Analyzer SHALL estimate cost impact in USD based on resources created during the compromise period.

### Requirement 7: Dynamic IAM Policy Generation

**User Story:** As a security architect, I want the system to generate minimal, resource-scoped IAM policies for remediation, so that the Remediation Agent receives only the permissions needed for specific quarantine actions.

#### Acceptance Criteria

1. WHEN an investigation is complete, THE Dynamic_Policy_Generator SHALL generate an IAM policy scoped to the specific resources discovered in the attack chain.
2. THE Dynamic_Policy_Generator SHALL generate separate policy statements for each resource type: IAM users/roles, EC2 instances, EC2 security groups, S3 buckets, Lambda functions, RDS instances, ECS tasks/clusters, DynamoDB tables, SNS topics, and SQS queues.
3. THE Dynamic_Policy_Generator SHALL include only the specific IAM actions required for quarantine operations on each resource type.
4. THE Dynamic_Policy_Generator SHALL scope each policy statement to the specific resource ARNs discovered in the attack chain.
5. THE Investigation_Agent SHALL attach the generated policy to the Remediation Agent IAM role before invoking remediation and detach it immediately after remediation completes.

### Requirement 8: Account Classification

**User Story:** As a security engineer, I want the system to classify accounts as production or non-production using AWS Organizations tags, so that remediation decisions respect account sensitivity.

#### Acceptance Criteria

1. THE Account_Classifier SHALL query AWS Organizations tags for the account using the configurable tag key (default: "Environment").
2. WHEN the tag value matches a configured production value (production, prod, prd), THE Account_Classifier SHALL classify the account as PRODUCTION.
3. WHEN the tag value matches a configured non-production value (development, dev, test, staging), THE Account_Classifier SHALL classify the account as NON_PRODUCTION.
4. IF the tag value is unrecognized or the Organizations API call fails, THEN THE Account_Classifier SHALL classify the account as PRODUCTION (safe default).
5. THE Account_Classifier SHALL cache classification results with a configurable TTL (default: 5 minutes) to reduce API calls.

### Requirement 9: Two-Agent Remediation Architecture

**User Story:** As a security architect, I want the system to separate investigation (read-only) from remediation (write) into two distinct agents, so that the blast radius of any single agent is minimized.

#### Acceptance Criteria

1. THE Investigation_Agent SHALL operate with read-only AWS permissions for CloudTrail, Security Hub, Trusted Advisor, AWS Health, and Organizations.
2. THE Remediation_Agent SHALL receive write permissions only through dynamically-attached IAM policies scoped to specific resources.
3. WHEN remediation is needed, THE Investigation_Agent SHALL invoke the Remediation_Agent via the Amazon Bedrock AgentCore invoke_agent_runtime API.
4. THE Remediation_Agent SHALL re-validate account classification before executing any quarantine actions (double safety check).
5. IF the Remediation_Agent account validation fails, THEN THE Remediation_Agent SHALL reject the remediation request and return an error.

### Requirement 10: Remediation Safety Controls

**User Story:** As a security engineer, I want the system to enforce safety controls that prevent unintended remediation of production resources, so that business-critical systems are protected.

#### Acceptance Criteria

1. WHILE an account is classified as PRODUCTION, THE Investigation_Agent SHALL send notifications only and skip auto-remediation.
2. WHILE an account is classified as NON_PRODUCTION and auto_remediation_enabled is true, THE Investigation_Agent SHALL proceed with automated remediation.
3. WHERE force_remediation is enabled, THE Investigation_Agent SHALL bypass the account type check and proceed with remediation regardless of classification.
4. WHERE dry_run_mode is enabled, THE Remediation_Agent SHALL log all quarantine actions without executing them.
5. IF the account classification is UNKNOWN, THEN THE Investigation_Agent SHALL treat the account as PRODUCTION and skip auto-remediation.

### Requirement 11: IAM User Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised IAM users by denying all access and deactivating keys, so that the attacker loses access immediately.

#### Acceptance Criteria

1. WHEN quarantining an IAM user, THE Remediation_Agent SHALL attach a deny-all inline policy (Deny * on *) to the user.
2. WHEN quarantining an IAM user, THE Remediation_Agent SHALL deactivate all active access keys for the user.
3. WHEN quarantining an IAM user, THE Remediation_Agent SHALL remove the user from all IAM groups.
4. THE Remediation_Agent SHALL record each quarantine action taken on the IAM user.

### Requirement 12: IAM Role Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised IAM roles, so that any services or users assuming the role lose access.

#### Acceptance Criteria

1. WHEN quarantining an IAM role, THE Remediation_Agent SHALL attach a deny-all inline policy (Deny * on *) to the role.
2. THE Remediation_Agent SHALL record each quarantine action taken on the IAM role.

### Requirement 13: EC2 Instance Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised EC2 instances by isolating network access and stopping the instance, so that the attacker cannot use the instance for further attacks.

#### Acceptance Criteria

1. WHEN quarantining an EC2 instance, THE Remediation_Agent SHALL create an isolated security group in the same VPC with no ingress or egress rules.
2. WHEN quarantining an EC2 instance, THE Remediation_Agent SHALL attach the isolated security group to the instance, replacing existing security groups.
3. WHEN quarantining an EC2 instance, THE Remediation_Agent SHALL detach the IAM instance profile from the instance.
4. WHEN quarantining an EC2 instance, THE Remediation_Agent SHALL stop the instance.
5. THE Remediation_Agent SHALL record each quarantine action taken on the EC2 instance.

### Requirement 14: EC2 Security Group Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised security groups by revoking all rules, so that any resources using the security group lose network access.

#### Acceptance Criteria

1. WHEN quarantining an EC2 security group, THE Remediation_Agent SHALL revoke all ingress rules.
2. WHEN quarantining an EC2 security group, THE Remediation_Agent SHALL revoke all egress rules.
3. WHEN quarantining an EC2 security group, THE Remediation_Agent SHALL tag the security group with quarantine metadata (QuarantinedBy, QuarantineReason).
4. THE Remediation_Agent SHALL record each quarantine action taken on the security group.

### Requirement 15: S3 Bucket Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised S3 buckets by blocking all external access, so that data exfiltration is prevented.

#### Acceptance Criteria

1. WHEN quarantining an S3 bucket, THE Remediation_Agent SHALL apply a deny-all bucket policy that blocks access from all principals except the owning account.
2. WHEN quarantining an S3 bucket, THE Remediation_Agent SHALL enable versioning on the bucket.
3. WHEN quarantining an S3 bucket, THE Remediation_Agent SHALL enable the public access block with all four flags set to true.
4. THE Remediation_Agent SHALL record each quarantine action taken on the S3 bucket.

### Requirement 16: Lambda Function Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised Lambda functions by disabling execution, so that malicious code cannot run.

#### Acceptance Criteria

1. WHEN quarantining a Lambda function, THE Remediation_Agent SHALL set reserved concurrent executions to 0 (disabling all invocations).
2. WHEN quarantining a Lambda function, THE Remediation_Agent SHALL remove all resource-based permissions from the function policy.
3. THE Remediation_Agent SHALL record each quarantine action taken on the Lambda function.

### Requirement 17: RDS Instance Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised RDS instances by isolating network access and stopping the instance, so that database access is revoked.

#### Acceptance Criteria

1. WHEN quarantining an RDS instance, THE Remediation_Agent SHALL create a snapshot for recovery purposes.
2. WHEN quarantining an RDS instance, THE Remediation_Agent SHALL create an isolated security group and modify the instance to use only that security group.
3. WHEN quarantining an RDS instance, THE Remediation_Agent SHALL stop the instance.
4. THE Remediation_Agent SHALL record each quarantine action taken on the RDS instance.

### Requirement 18: ECS Task Quarantine

**User Story:** As a security engineer, I want the system to quarantine compromised ECS tasks by stopping them, so that malicious containers cannot continue running.

#### Acceptance Criteria

1. WHEN quarantining an ECS task, THE Remediation_Agent SHALL stop the task with a reason indicating quarantine due to compromised access key.
2. THE Remediation_Agent SHALL record each quarantine action taken on the ECS task.

### Requirement 19: Resource Tagging

**User Story:** As a security engineer, I want all quarantined resources to be tagged with quarantine metadata, so that the quarantine status is visible and auditable.

#### Acceptance Criteria

1. WHEN a resource is quarantined, THE Remediation_Agent SHALL tag the resource with QuarantinedBy set to "AccessKeyCompromiseAgent".
2. WHEN a resource is quarantined, THE Remediation_Agent SHALL tag the resource with QuarantineReason set to "CompromisedAccessKey".
3. WHEN a resource is quarantined, THE Remediation_Agent SHALL tag the resource with a QuarantineTimestamp set to the current UTC time.

### Requirement 20: Multi-Channel Notifications

**User Story:** As a security engineer, I want the system to send investigation reports and alerts through multiple notification channels, so that the security team is informed regardless of their preferred communication tool.

#### Acceptance Criteria

1. THE Investigation_Agent SHALL support notification delivery via Amazon SNS, email (Amazon SES), Slack webhooks, Microsoft Teams webhooks, and PagerDuty.
2. WHEN an investigation completes, THE Investigation_Agent SHALL dispatch an alert to all enabled notification channels containing: severity, account ID, compromised key, username, resources affected, risk level, and cost impact.
3. IF a notification channel fails to deliver, THEN THE Investigation_Agent SHALL retry with exponential backoff up to 3 attempts.
4. THE Investigation_Agent SHALL format messages appropriately for each channel (JSON for SNS, markdown for Slack/Teams, structured payload for PagerDuty).

### Requirement 21: Intelligent Paging Rules

**User Story:** As a security engineer, I want the system to apply intelligent paging rules based on account type, severity, and business context, so that the on-call team is paged only when appropriate.

#### Acceptance Criteria

1. WHILE an account is classified as PRODUCTION, THE Paging_Engine SHALL always page the security team (configurable severity threshold, default: MEDIUM).
2. WHILE an account is classified as NON_PRODUCTION, THE Paging_Engine SHALL page only when: risk level is HIGH or CRITICAL, resources were created by the attacker, or cost impact exceeds a configurable threshold (default: $100 USD).
3. WHILE an account classification is UNKNOWN, THE Paging_Engine SHALL apply production paging rules (safe default).
4. THE Paging_Engine SHALL support configurable business hours (start hour, end hour, timezone, weekdays only).

### Requirement 22: Investigation Report Generation

**User Story:** As a security analyst, I want the system to generate comprehensive markdown investigation reports, so that I have a complete record of the compromise and response.

#### Acceptance Criteria

1. THE Investigation_Agent SHALL generate a report containing: header (key ID, username, account, severity, source, timestamp), executive summary, activity timeline, API activity analysis, resource activity, IP address analysis, behavioral analysis, cost impact assessment, and recommended actions.
2. THE Investigation_Agent SHALL include the top 10 services by API call count and top 10 API actions by frequency in the API activity section.
3. THE Investigation_Agent SHALL list all resources created, modified, and deleted with resource type, ID, region, and timestamp.
4. THE Investigation_Agent SHALL list all unique IP addresses with request count, first seen, and last seen timestamps.
5. THE Investigation_Agent SHALL include all detected behavioral anomalies with type, severity, and description.

### Requirement 23: YAML-Based Configuration

**User Story:** As a platform engineer, I want the system to be configurable via a YAML file, so that behavior can be tuned without code changes.

#### Acceptance Criteria

1. THE Investigation_Agent SHALL load configuration from a YAML file at a configurable path (default: config/agent_config.yaml).
2. THE Investigation_Agent SHALL support configuration of: analysis lookback days, historical baseline days, remediation enabled/disabled, dry-run mode, force remediation flag, alert channels, paging rules, Organizations tag key, production values, resource discovery timeout, and feature flags.
3. IF the configuration file is not found, THEN THE Investigation_Agent SHALL use safe default values (dry-run enabled, remediation disabled, 7-day lookback, 90-day baseline).
4. THE Investigation_Agent SHALL support feature flags to enable or disable individual detection sources (AWS Health, Security Hub, Trusted Advisor) and capabilities (historical baseline, transitive discovery, anomaly detection, auto-remediation).

### Requirement 24: Infrastructure as Code Deployment

**User Story:** As a platform engineer, I want the system infrastructure to be deployable via a single CloudFormation template, so that the event-driven pipeline can be set up consistently.

#### Acceptance Criteria

1. THE CloudFormation template SHALL deploy: an AWS Lambda function (Python 3.12, ARM64), three EventBridge rules (AWS Health, Security Hub, Trusted Advisor), an Amazon SNS topic with KMS encryption, an SQS dead-letter queue, and IAM roles with least-privilege policies.
2. THE CloudFormation template SHALL accept parameters for: notification email, Investigation Agent runtime ID, Remediation Agent runtime ID, Investigation Agent role name, and Remediation Agent role name.
3. THE CloudFormation template SHALL configure Lambda permissions allowing EventBridge to invoke the function.
4. THE CloudFormation template SHALL configure the Investigation Agent role with permissions to: publish to SNS, invoke the Remediation Agent, and manage the Remediation Agent's IAM policies (put/delete role policy).

### Requirement 25: Quarantine-Only Remediation Philosophy

**User Story:** As a security architect, I want the system to quarantine resources rather than delete them, so that forensic evidence is preserved and recovery is possible.

#### Acceptance Criteria

1. THE Remediation_Agent SHALL isolate resources by denying access, stopping execution, or revoking network rules — never by deleting resources.
2. THE Remediation_Agent SHALL preserve all resource state and data for forensic analysis.
3. WHEN quarantining an RDS instance, THE Remediation_Agent SHALL create a snapshot before modifying security groups.
4. WHEN quarantining an S3 bucket, THE Remediation_Agent SHALL enable versioning to preserve object history.
