# Comprehensive Threat Model Report

**Generated**: 2026-07-31 13:42:04
**Current Phase**: 1 - Business Context Analysis
**Overall Completion**: 100.0%

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Context](#business-context)
3. [System Architecture](#system-architecture)
4. [Threat Actors](#threat-actors)
5. [Trust Boundaries](#trust-boundaries)
6. [Assets and Flows](#assets-and-flows)
7. [Threats](#threats)
8. [Mitigations](#mitigations)
9. [Assumptions](#assumptions)
10. [Phase Progress](#phase-progress)

## Executive Summary

MIO Agent (Monitoring Intelligence and Observability Agent) is an agentic AI system built on Amazon Bedrock that continuously assesses the monitoring and observability posture of AWS customer environments. It is used by AWS Technical Account Managers (TAMs) to proactively identify observability gaps in customer AWS accounts before production incidents expose blind spots. The agent analyzes CloudWatch configuration, IaC templates, and third-party monitoring coverage, then generates prioritized gap reports with specific recommendations. It operates in a TAM-facing tool context where the TAM uses findings to advise customers.

### Key Statistics

- **Total Threats**: 17
- **Total Mitigations**: 26
- **Total Assumptions**: 4
- **System Components**: 19
- **Assets**: 15
- **Threat Actors**: 16

## Business Context

**Description**: MIO Agent (Monitoring Intelligence and Observability Agent) is an agentic AI system built on Amazon Bedrock that continuously assesses the monitoring and observability posture of AWS customer environments. It is used by AWS Technical Account Managers (TAMs) to proactively identify observability gaps in customer AWS accounts before production incidents expose blind spots. The agent analyzes CloudWatch configuration, IaC templates, and third-party monitoring coverage, then generates prioritized gap reports with specific recommendations. It operates in a TAM-facing tool context where the TAM uses findings to advise customers.

### Business Features

- **Industry Sector**: Technology
- **Data Sensitivity**: Confidential
- **User Base Size**: Medium
- **Geographic Scope**: Global
- **Regulatory Requirements**: None
- **System Criticality**: High
- **Financial Impact**: Medium
- **Authentication Requirement**: Federated
- **Deployment Environment**: Cloud-Public
- **Integration Complexity**: Complex

## System Architecture

### Components

| ID | Name | Type | Service Provider | Description |
|---|---|---|---|---|
| C001 | API Gateway | Network | AWS | REST API entry point for on-demand assessment requests. IAM authorization on all endpoints, throttling 10 req/s sustained / 20 burst, X-Ray tracing enabled. |
| C002 | API Handler Lambda | Compute | AWS | Routes API Gateway requests to orchestrator. Handles POST /assess, GET /assess/{id}, GET /accounts, GET /accounts/{id}/history, POST /reports/{id}/approve, POST /feedback. Python 3.12, 512MB, 10-min timeout. |
| C003 | Coordinator Lambda | Compute | AWS | Orchestrates full assessment workflow. Runs CloudWatch, IaC, and third-party sub-agents in parallel (ThreadPoolExecutor, max_workers=3). Calls Bedrock for narrative generation. 1024MB, 10-min timeout, reserved_concurrency=10. |
| C004 | Scheduler Lambda | Compute | AWS | Triggered by EventBridge weekly cron (Monday 08:00 UTC) for batch assessments of all enabled accounts. |
| C005 | Health Event Lambda | Compute | AWS | Triggered by AWS Health events via EventBridge. Triggers assessment when a health event affects a monitored account. |
| C006 | Deployment Monitor Lambda | Compute | AWS | Triggered by CloudTrail deployment events (RunInstances, CreateFunction, CreateDBInstance, CreateCluster, CreateRestApi) via EventBridge. Checks if monitoring was provisioned for new resources. |
| C007 | Support Case Lambda | Compute | AWS | Triggered on P1/P2 support case creation. Triggers targeted assessment to correlate support case context with observability gaps. |
| C008 | Assessments DynamoDB Table | Storage | AWS | Persists OMS scores and findings JSON per customer account. PK: account_id, SK: assessment_timestamp. TTL: 365 days. PITR enabled. AWS-managed encryption. |
| C009 | Accounts DynamoDB Table | Storage | AWS | Stores customer account configurations: account_id, account_name, access_tier, role_arn, tam_alias, enabled flag. PITR enabled. AWS-managed encryption. |
| C010 | Reviews DynamoDB Table | Storage | AWS | Human-in-the-loop review records for customer/leadership reports. Tracks PENDING_REVIEW, APPROVED, REJECTED, EXPIRED, AUTO_APPROVED statuses. 48-hour review window. PK: report_id. PITR enabled. |
| C011 | Feedback DynamoDB Table | Storage | AWS | TAM accuracy feedback on individual findings. Feeds quality improvement loop. PK: finding_id, SK: feedback_timestamp. TTL: 365 days. PITR enabled. |
| C012 | Reports S3 Bucket | Storage | AWS | Stores generated assessment reports (TAM briefs, customer reports, leadership summaries) as markdown. Server-side encryption AES256. Block public access. SSL enforced. Versioned. Lifecycle: 365-day expiry. |
| C013 | Assessment SQS Queue | Messaging | AWS | Assessment request queue consumed by Coordinator Lambda. Visibility timeout 10 min. SQS-managed encryption. DLQ after 3 receive attempts (14-day retention). |
| C014 | EventBridge | Messaging | AWS | Routes trigger events to appropriate Lambda functions: aws.health events, CloudTrail deployment events, weekly cron schedule, support case events. |
| C015 | SSM Parameter Store | Storage | AWS | Stores SQS queue URL and other runtime configuration under /mio-agent/* path prefix. Lambda role scoped to this prefix only. |
| C016 | Amazon Bedrock | Other | AWS | LLM inference endpoint for narrative generation. Default model: Claude Sonnet 4. Bedrock Guardrails applied for content filtering and PII redaction. |
| C017 | Guardrail Pipeline | Security | Other | 5-layer guardrail pipeline: InputValidation, FindingValidation, ConfidenceGate, BedrockGuardrails, HumanReviewGate. Prompt injection protection, OMS caps per access tier. |
| C018 | Customer AWS Account | Other | AWS | External customer AWS account accessed via STS AssumeRole for Tier 3 assessments. Read-only role MIOAgentReadOnly. Never writes to customer environment. |
| C019 | TAM User | Other | Other | AWS Technical Account Manager. Invokes agent via API, reviews pending reports, provides feedback, shares approved output with customers. Authenticates via IAM SigV4. |

### Data Stores

| ID | Name | Type | Classification | Encrypted at Rest | Description |
|---|---|---|---|---|---|
| D001 | Assessment History Store | Relational | Confidential | Yes | OMS scores, finding details (JSON), risk levels, assessment timestamps, trend data per customer account. Contains customer AWS account IDs and observability gap analysis. |
| D002 | Accounts Configuration Store | Relational | Confidential | Yes | Customer account configurations: account IDs, account names, access tiers, IAM role ARNs, TAM aliases, enabled status. Contains customer PII-adjacent data. |
| D003 | Review Records Store | Relational | Internal | Yes | Human review audit records for generated reports. Tracks who reviewed what, approval/rejection status, timestamps, review notes. |
| D004 | Finding Feedback Store | Relational | Internal | Yes | TAM accuracy feedback on individual findings. Feeds quality improvement loop. Finding IDs, accuracy ratings, TAM aliases, notes. |
| D005 | SSM Parameter Store | NoSQL | Internal | Yes | Runtime configuration including SQS queue URL. Path-prefix restricted to /mio-agent/*. |
| D006 | Reports Object Store | Object Storage | Confidential | Yes | Generated assessment reports: TAM briefs, customer-facing reports, leadership summaries. Stored as markdown. AES256 server-side encryption. S3 versioning enabled. 365-day lifecycle expiry. |

## Threat Actors

### Insider

- **Type**: ThreatActorType.INSIDER
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Revenge
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 5/10
- **Description**: An employee or contractor with legitimate access to the system

### External Attacker

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 3/10
- **Description**: An external individual or group attempting to gain unauthorized access

### Nation-state Actor

- **Type**: ThreatActorType.NATION_STATE
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Espionage, Political
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 1/10
- **Description**: A government-sponsored group with advanced capabilities

### Hacktivist

- **Type**: ThreatActorType.HACKTIVIST
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Ideology, Political
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 6/10
- **Description**: An individual or group motivated by ideological or political beliefs

### Organized Crime

- **Type**: ThreatActorType.ORGANIZED_CRIME
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 2/10
- **Description**: A criminal organization with significant resources

### Competitor

- **Type**: ThreatActorType.COMPETITOR
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Espionage
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 7/10
- **Description**: A business competitor seeking competitive advantage

### Script Kiddie

- **Type**: ThreatActorType.SCRIPT_KIDDIE
- **Capability Level**: CapabilityLevel.LOW
- **Motivations**: Curiosity, Reputation
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 9/10
- **Description**: An inexperienced attacker using pre-made tools

### Disgruntled Employee

- **Type**: ThreatActorType.DISGRUNTLED_EMPLOYEE
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Revenge
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 4/10
- **Description**: A current or former employee with a grievance

### Privileged User

- **Type**: ThreatActorType.PRIVILEGED_USER
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial, Accidental
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 8/10
- **Description**: A user with elevated privileges who may abuse them or make mistakes

### Third Party

- **Type**: ThreatActorType.THIRD_PARTY
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Financial, Accidental
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 10/10
- **Description**: A vendor, partner, or service provider with access to the system

### External Attacker

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial, Espionage
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 1/10
- **Description**: Unauthenticated external adversary attempting to exploit the API Gateway endpoint, abuse presigned S3 URLs, or inject malicious content into assessment inputs to exfiltrate customer account data or manipulate observability reports.

### Supply Chain / Dependency Attacker

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Espionage, Financial
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 3/10
- **Description**: Adversary who compromises a Python dependency (e.g., boto3, pydantic, strands-agents) to inject malicious code into the Lambda execution environment, potentially exfiltrating credentials or customer data at scale.

### Prompt Injection Attacker

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Disruption, Espionage
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 4/10
- **Description**: Adversary who crafts malicious content in customer AWS resource names, tags, CloudFormation templates, or IaC files that the agent reads, attempting to hijack Bedrock LLM reasoning and generate false findings or exfiltrate system prompt content.

### Nation-State Actor

- **Type**: ThreatActorType.NATION_STATE
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Espionage
- **Resources**: ResourceLevel.EXTENSIVE
- **Relevant**: Yes
- **Priority**: 6/10
- **Description**: Sophisticated state-sponsored adversary targeting the agent's access to customer AWS account inventories and observability gap data across many enterprise accounts for intelligence gathering.

### Malicious Insider (Rogue TAM)

- **Type**: ThreatActorType.INSIDER
- **Capability Level**: CapabilityLevel.HIGH
- **Motivations**: Financial, Revenge
- **Resources**: ResourceLevel.MODERATE
- **Relevant**: Yes
- **Priority**: 2/10
- **Description**: A TAM with valid IAM credentials who abuses API access to exfiltrate customer account IDs/role ARNs, approve fraudulent reports, or extract assessment reports from S3 for personal gain.

### Compromised Customer Account

- **Type**: ThreatActorType.EXTERNAL
- **Capability Level**: CapabilityLevel.MEDIUM
- **Motivations**: Disruption, Espionage
- **Resources**: ResourceLevel.LIMITED
- **Relevant**: Yes
- **Priority**: 5/10
- **Description**: An adversary who has compromised a monitored customer AWS account and plants adversarial data (malicious resource names, tags, IaC templates) to manipulate assessment findings when the agent reads via the MIOAgentReadOnly role.

## Trust Boundaries

### Trust Zones

#### Internet

- **Trust Level**: TrustLevel.UNTRUSTED
- **Description**: The public internet, considered untrusted

#### DMZ

- **Trust Level**: TrustLevel.LOW
- **Description**: Demilitarized zone for public-facing services

#### Application

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: Zone containing application servers and services

#### Data

- **Trust Level**: TrustLevel.HIGH
- **Description**: Zone containing databases and data storage

#### Admin

- **Trust Level**: TrustLevel.FULL
- **Description**: Administrative zone with highest privileges

#### Public Internet

- **Trust Level**: TrustLevel.UNTRUSTED
- **Description**: Internet-facing zone accessible by TAMs and any unauthenticated caller. API Gateway sits at the boundary of this zone.

#### API Gateway Perimeter

- **Trust Level**: TrustLevel.LOW
- **Description**: AWS-managed API Gateway layer providing IAM SigV4 authentication, throttling, and TLS termination. First authenticated zone.

#### Lambda Execution Zone

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: AWS Lambda execution environment with VPC-optional placement. Contains all Lambda functions with dedicated IAM execution roles.

#### Bedrock Inference Zone

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: Amazon Bedrock service boundary. Inference calls from Lambda cross this zone boundary. Guardrails enforced here.

#### Data Storage Zone

- **Trust Level**: TrustLevel.HIGH
- **Description**: AWS-managed data persistence layer: DynamoDB tables, S3 reports bucket, SQS queue. All encrypted at rest.

#### Customer AWS Account Zone

- **Trust Level**: TrustLevel.LOW
- **Description**: External customer AWS account accessed via STS AssumeRole (MIOAgentReadOnly). Read-only access. Considered partially untrusted as content is customer-controlled.

#### AWS Event Bus Zone

- **Trust Level**: TrustLevel.MEDIUM
- **Description**: EventBridge and CloudTrail service zone. Receives events from customer account activity and AWS Health.

### Trust Boundaries

#### Internet-to-API Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: IAM SigV4 authentication, TLS 1.2+ encryption in transit, API throttling 10 req/s / 20 burst, IAM authorization on all API methods
- **Description**: Boundary between the public internet and API Gateway. First line of defence — only IAM-authenticated requests cross this boundary.

#### Cross-Account Trust Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: STS AssumeRole scoped to MIOAgentReadOnly pattern only, Read-only IAM policy — no write permissions to customer resources, Account ID registered in mio-agent-accounts before access granted, Cross-account access only to registered accounts
- **Description**: Boundary between MIO Agent infrastructure and external customer AWS accounts. Highest privilege boundary — cross-account IAM role assumption.

#### EventBridge-to-Lambda Boundary

- **Type**: BoundaryType.NETWORK
- **Controls**: EventBridge resource-based policy, Event pattern filtering (specific event sources and names only), CloudTrail delivery authentication
- **Description**: Boundary between AWS event bus and Lambda trigger functions. EventBridge rules restrict which event patterns invoke which Lambda.

#### API-to-Lambda Boundary

- **Type**: BoundaryType.PROCESS
- **Controls**: Lambda execution role (least-privilege), IAM authorization on Lambda invoke, Environment variable injection only — no secrets in code
- **Description**: Boundary between API Gateway perimeter and Lambda execution environment.

#### Lambda-to-Bedrock Boundary

- **Type**: BoundaryType.PROCESS
- **Controls**: IAM role BedrockInvoke policy, Bedrock Guardrails (content filtering, PII redaction), Prompt injection sanitization in input_validator.py before Bedrock call
- **Description**: Boundary between Lambda compute and Amazon Bedrock LLM inference. High risk for prompt injection and LLM output manipulation.

#### Lambda-to-DataStore Boundary

- **Type**: BoundaryType.OTHER
- **Controls**: IAM role scoped to specific table/bucket ARNs, DynamoDB PITR + AWS-managed encryption, S3 block public access + SSL enforcement + AES256, SQS SQS-managed encryption + DLQ
- **Description**: Boundary between Lambda compute and persistent data stores (DynamoDB, S3, SQS).

## Assets and Flows

### Assets

| ID | Name | Type | Classification | Sensitivity | Criticality | Owner |
|---|---|---|---|---|---|---|
| A001 | User Credentials | AssetType.CREDENTIAL | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A002 | Personal Identifiable Information | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 4 | N/A |
| A003 | Session Token | AssetType.TOKEN | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A004 | Configuration Data | AssetType.CONFIG | AssetClassification.INTERNAL | 3 | 4 | N/A |
| A005 | Encryption Keys | AssetType.KEY | AssetClassification.RESTRICTED | 5 | 5 | N/A |
| A006 | Public Content | AssetType.DATA | AssetClassification.PUBLIC | 1 | 2 | N/A |
| A007 | Audit Logs | AssetType.DATA | AssetClassification.INTERNAL | 3 | 4 | N/A |
| A008 | Customer Account Credentials | AssetType.CREDENTIAL | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A009 | Assessment Findings Data | AssetType.DATA | AssetClassification.CONFIDENTIAL | 5 | 5 | N/A |
| A010 | Generated Assessment Reports | AssetType.DATA | AssetClassification.CONFIDENTIAL | 4 | 4 | N/A |
| A011 | Cross-Account IAM Role ARN | AssetType.CREDENTIAL | AssetClassification.INTERNAL | 5 | 4 | N/A |
| A012 | Bedrock System Prompts | AssetType.DATA | AssetClassification.INTERNAL | 3 | 3 | N/A |
| A013 | Review Audit Records | AssetType.DATA | AssetClassification.INTERNAL | 3 | 3 | N/A |
| A014 | Lambda Execution Credentials | AssetType.CREDENTIAL | AssetClassification.INTERNAL | 5 | 4 | N/A |
| A015 | Presigned S3 Report URLs | AssetType.CREDENTIAL | AssetClassification.INTERNAL | 3 | 2 | N/A |

### Asset Flows

| ID | Asset | Source | Destination | Protocol | Encrypted | Risk Level |
|---|---|---|---|---|---|---|
| F001 | User Credentials | C001 | C002 | HTTPS | Yes | 4 |
| F002 | Session Token | C002 | C001 | HTTPS | Yes | 3 |
| F003 | Personal Identifiable Information | C003 | C004 | TLS | Yes | 3 |
| F004 | Audit Logs | C003 | C005 | TLS | Yes | 2 |
| F005 | Assessment Findings Data | C019 | C001 | HTTPS | Yes | 2 |
| F006 | Assessment Findings Data | C001 | C002 | HTTPS | Yes | 2 |
| F007 | Assessment Findings Data | C002 | C003 | HTTPS | Yes | 3 |
| F008 | User Credentials | C003 | C018 | HTTPS | Yes | 5 |
| F009 | Assessment Findings Data | C003 | C016 | HTTPS | Yes | 4 |
| F010 | Assessment Findings Data | C003 | C009 | HTTPS | Yes | 3 |
| F011 | Generated Assessment Reports | C003 | C013 | HTTPS | Yes | 3 |
| F012 | Generated Assessment Reports | C013 | C019 | HTTPS | Yes | 3 |
| F013 | Review Audit Records | C017 | C011 | HTTPS | Yes | 2 |
| F014 | Review Audit Records | C019 | C001 | HTTPS | Yes | 3 |
| F015 | User Credentials | C004 | C010 | HTTPS | Yes | 3 |
| F016 | User Credentials | C015 | C005 | HTTPS | Yes | 3 |

## Threats

### Resolved Threats

#### T1: External attacker or compromised TAM credentials

**Statement**: A External attacker or compromised TAM credentials Attacker obtains or forges valid AWS IAM credentials for a TAM user can forge or replay IAM SigV4 signed API requests to POST /assess or POST /reports/{id}/approve, which leads to unauthorized assessment triggers or fraudulent report approvals delivered to customers

- **Prerequisites**: Attacker obtains or forges valid AWS IAM credentials for a TAM user
- **Action**: forge or replay IAM SigV4 signed API requests to POST /assess or POST /reports/{id}/approve
- **Impact**: unauthorized assessment triggers or fraudulent report approvals delivered to customers
- **Impacted Assets**: A009, A010, A011
- **Tags**: api, iam, authentication

#### T4: Malicious insider or external attacker with data store write access

**Statement**: A Malicious insider or external attacker with data store write access Attacker has write access to DynamoDB assessments table or S3 reports bucket via misconfigured IAM or compromised Lambda role can modify stored assessment findings in DynamoDB or overwrite S3 report objects to alter OMS scores or inject false recommendations, which leads to customer receives manipulated observability assessment; TAM makes incorrect recommendations; trust in agent output destroyed

- **Prerequisites**: Attacker has write access to DynamoDB assessments table or S3 reports bucket via misconfigured IAM or compromised Lambda role
- **Action**: modify stored assessment findings in DynamoDB or overwrite S3 report objects to alter OMS scores or inject false recommendations
- **Impact**: customer receives manipulated observability assessment; TAM makes incorrect recommendations; trust in agent output destroyed
- **Impacted Assets**: A009, A010
- **Tags**: dynamodb, s3, data-integrity

#### T6: External attacker with SQS write access

**Statement**: A External attacker with SQS write access Attacker can send messages to the assessment SQS queue via misconfigured queue policy or IAM escalation can inject malformed or adversarial assessment request messages into the SQS queue to trigger unintended assessments or cause Lambda processing errors, which leads to unexpected assessments triggered; potential Bedrock quota exhaustion or excessive cross-account role assumptions

- **Prerequisites**: Attacker can send messages to the assessment SQS queue via misconfigured queue policy or IAM escalation
- **Action**: inject malformed or adversarial assessment request messages into the SQS queue to trigger unintended assessments or cause Lambda processing errors
- **Impact**: unexpected assessments triggered; potential Bedrock quota exhaustion or excessive cross-account role assumptions
- **Impacted Assets**: A009
- **Tags**: sqs, queue, message-tampering

#### T8: Malicious insider

**Statement**: A Malicious insider S3 versioning or access logging disabled can delete or overwrite S3 report objects and deny having generated a specific assessment outcome, which leads to inability to prove what was delivered to customer; compliance and legal exposure for incorrect reports

- **Prerequisites**: S3 versioning or access logging disabled
- **Action**: delete or overwrite S3 report objects and deny having generated a specific assessment outcome
- **Impact**: inability to prove what was delivered to customer; compliance and legal exposure for incorrect reports
- **Impacted Assets**: A010
- **Tags**: s3, logging, audit

#### T9: External attacker or malicious insider with data store access

**Statement**: A External attacker or malicious insider with data store access Misconfigured IAM policy grants overly broad DynamoDB or S3 read access, or Lambda execution credentials leaked can read all customer account IDs, IAM role ARNs, OMS scores, and findings JSON from DynamoDB tables or enumerate all reports from S3, which leads to mass exfiltration of customer AWS account configurations, observability gap intelligence, and IAM role ARNs enabling further cross-account attacks

- **Prerequisites**: Misconfigured IAM policy grants overly broad DynamoDB or S3 read access, or Lambda execution credentials leaked
- **Action**: read all customer account IDs, IAM role ARNs, OMS scores, and findings JSON from DynamoDB tables or enumerate all reports from S3
- **Impact**: mass exfiltration of customer AWS account configurations, observability gap intelligence, and IAM role ARNs enabling further cross-account attacks
- **Impacted Assets**: A009, A010, A011
- **Tags**: dynamodb, s3, data-exfiltration

#### T13: External attacker or accidental misuse

**Statement**: A External attacker or accidental misuse Attacker has valid IAM credentials (low bar for internal tool) can flood POST /assess endpoint to exhaust API Gateway throttle limits, Lambda concurrency, or Bedrock model invocation TPM quotas, which leads to MIO Agent becomes unavailable; legitimate TAM assessments blocked; Bedrock quota exhausted affecting other account workloads

- **Prerequisites**: Attacker has valid IAM credentials (low bar for internal tool)
- **Action**: flood POST /assess endpoint to exhaust API Gateway throttle limits, Lambda concurrency, or Bedrock model invocation TPM quotas
- **Impact**: MIO Agent becomes unavailable; legitimate TAM assessments blocked; Bedrock quota exhausted affecting other account workloads
- **Impacted Assets**: A009, A010
- **Tags**: dos, bedrock, quota, lambda

### Identified Threats

#### T2: External attacker with knowledge of role ARN pattern

**Statement**: A External attacker with knowledge of role ARN pattern Attacker compromises Lambda execution environment or exfiltrates IAM role ARN from Accounts table can forge an AssumeRole request using a stolen MIOAgentReadOnly role ARN to gain cross-account access to customer AWS accounts, which leads to unauthorized read access to customer AWS resources; data exfiltration of CloudWatch metrics, EC2/RDS inventory, IaC templates

- **Prerequisites**: Attacker compromises Lambda execution environment or exfiltrates IAM role ARN from Accounts table
- **Action**: forge an AssumeRole request using a stolen MIOAgentReadOnly role ARN to gain cross-account access to customer AWS accounts
- **Impact**: unauthorized read access to customer AWS resources; data exfiltration of CloudWatch metrics, EC2/RDS inventory, IaC templates
- **Impacted Assets**: A011
- **Tags**: cross-account, sts, iam

#### T3: Malicious insider (rogue TAM)

**Statement**: A Malicious insider (rogue TAM) Attacker has valid IAM credentials for any TAM user can impersonate a different TAM alias in the reviewed_by field when calling POST /reports/{id}/approve, which leads to fraudulent review approval attributed to another TAM; audit trail integrity compromised

- **Prerequisites**: Attacker has valid IAM credentials for any TAM user
- **Action**: impersonate a different TAM alias in the reviewed_by field when calling POST /reports/{id}/approve
- **Impact**: fraudulent review approval attributed to another TAM; audit trail integrity compromised
- **Impacted Assets**: A013
- **Tags**: human-review, approval, audit

#### T5: Prompt injection attacker or compromised customer account

**Statement**: A Prompt injection attacker or compromised customer account Adversary controls customer AWS resource names, tags, CloudFormation template content, or IaC parameter values can inject prompt injection payloads into customer-controlled data (resource names, tags, IaC templates) read by Coordinator Lambda and passed to Amazon Bedrock, which leads to Bedrock generates false findings, leaks system prompt, or produces manipulated report content delivered to customer

- **Prerequisites**: Adversary controls customer AWS resource names, tags, CloudFormation template content, or IaC parameter values
- **Action**: inject prompt injection payloads into customer-controlled data (resource names, tags, IaC templates) read by Coordinator Lambda and passed to Amazon Bedrock
- **Impact**: Bedrock generates false findings, leaks system prompt, or produces manipulated report content delivered to customer
- **Impacted Assets**: A012
- **Tags**: prompt-injection, bedrock, llm

#### T7: Malicious insider (rogue TAM) with DynamoDB access

**Statement**: A Malicious insider (rogue TAM) with DynamoDB access Review records can be deleted or TTL manipulated by entity with DynamoDB write access can delete or modify review audit records in Reviews DynamoDB table to erase evidence of who approved a report or when, which leads to TAM cannot be held accountable for report approvals; audit trail for compliance and incident investigation destroyed

- **Prerequisites**: Review records can be deleted or TTL manipulated by entity with DynamoDB write access
- **Action**: delete or modify review audit records in Reviews DynamoDB table to erase evidence of who approved a report or when
- **Impact**: TAM cannot be held accountable for report approvals; audit trail for compliance and incident investigation destroyed
- **Impacted Assets**: A013
- **Tags**: audit, non-repudiation, dynamodb

#### T10: External attacker intercepting URL in email or chat

**Statement**: A External attacker intercepting URL in email or chat Presigned URL intercepted in transit or shared beyond intended recipient can intercept or obtain a presigned S3 report URL and access confidential customer assessment report without IAM credentials, which leads to unauthorized access to customer-specific observability gap report; disclosure of sensitive infrastructure details

- **Prerequisites**: Presigned URL intercepted in transit or shared beyond intended recipient
- **Action**: intercept or obtain a presigned S3 report URL and access confidential customer assessment report without IAM credentials
- **Impact**: unauthorized access to customer-specific observability gap report; disclosure of sensitive infrastructure details
- **Impacted Assets**: A015
- **Tags**: s3, presigned-url, report-sharing

#### T11: Prompt injection attacker

**Statement**: A Prompt injection attacker Adversary successfully executes prompt injection via customer-controlled data read by the agent can craft prompt injection payload that causes Bedrock to include the system prompt or internal tool configuration in generated report output, which leads to disclosure of system prompt reveals agent reasoning strategy, guardrail logic, and internal tool names; enables more targeted attacks

- **Prerequisites**: Adversary successfully executes prompt injection via customer-controlled data read by the agent
- **Action**: craft prompt injection payload that causes Bedrock to include the system prompt or internal tool configuration in generated report output
- **Impact**: disclosure of system prompt reveals agent reasoning strategy, guardrail logic, and internal tool names; enables more targeted attacks
- **Impacted Assets**: A012
- **Tags**: prompt-injection, bedrock, system-prompt

#### T12: External attacker exploiting SSRF or error handling vulnerability

**Statement**: A External attacker exploiting SSRF or error handling vulnerability Lambda execution environment misconfiguration or SSRF vulnerability exposes IMDS endpoint can access Lambda execution credentials via IMDS/SSRF or expose them in error responses, which leads to attacker obtains temporary AWS credentials with full MIO Agent Lambda role access to all data stores and cross-account role assumption

- **Prerequisites**: Lambda execution environment misconfiguration or SSRF vulnerability exposes IMDS endpoint
- **Action**: access Lambda execution credentials via IMDS/SSRF or expose them in error responses
- **Impact**: attacker obtains temporary AWS credentials with full MIO Agent Lambda role access to all data stores and cross-account role assumption
- **Impacted Assets**: A014
- **Tags**: lambda, credentials, ssrf

#### T14: External attacker or misconfigured scheduler

**Statement**: A External attacker or misconfigured scheduler Attacker can trigger many Tier 3 assessment requests can trigger mass concurrent Tier 3 assessments to exhaust STS AssumeRole API rate limits and AWS service API quotas in customer accounts, which leads to customer AWS account API rate limits exhausted; MIO Agent blocked from legitimate cross-account assessments

- **Prerequisites**: Attacker can trigger many Tier 3 assessment requests
- **Action**: trigger mass concurrent Tier 3 assessments to exhaust STS AssumeRole API rate limits and AWS service API quotas in customer accounts
- **Impact**: customer AWS account API rate limits exhausted; MIO Agent blocked from legitimate cross-account assessments
- **Impacted Assets**: A009
- **Tags**: sts, cross-account, quota

#### T15: Supply chain attacker or external attacker exploiting Lambda vulnerability

**Statement**: A Supply chain attacker or external attacker exploiting Lambda vulnerability Critical vulnerability in Python runtime, boto3, or Lambda execution environment can exploit a Lambda sandbox escape or compromised dependency to access raw Lambda execution role credentials or escalate IAM permissions, which leads to complete compromise of MIO Agent infrastructure; access to all data stores and cross-account role assumption capability

- **Prerequisites**: Critical vulnerability in Python runtime, boto3, or Lambda execution environment
- **Action**: exploit a Lambda sandbox escape or compromised dependency to access raw Lambda execution role credentials or escalate IAM permissions
- **Impact**: complete compromise of MIO Agent infrastructure; access to all data stores and cross-account role assumption capability
- **Impacted Assets**: A014
- **Tags**: lambda, iam, privilege-escalation, supply-chain

#### T16: External attacker or misconfigured IAM

**Statement**: A External attacker or misconfigured IAM Wildcard account ID in STS AssumeRole resource condition or customer-misconfigured trust policy can assume MIOAgentReadOnly role in unregistered accounts by exploiting wildcard in arn:aws:iam::*:role/MIOAgentReadOnly resource or pivot to higher-privilege roles via misconfigured customer trust policy, which leads to unauthorized cross-account access to customer accounts beyond the registered list

- **Prerequisites**: Wildcard account ID in STS AssumeRole resource condition or customer-misconfigured trust policy
- **Action**: assume MIOAgentReadOnly role in unregistered accounts by exploiting wildcard in arn:aws:iam::*:role/MIOAgentReadOnly resource or pivot to higher-privilege roles via misconfigured customer trust policy
- **Impact**: unauthorized cross-account access to customer accounts beyond the registered list
- **Impacted Assets**: A011, A014
- **Tags**: cross-account, sts, privilege-escalation

#### T17: Malicious insider (rogue TAM)

**Statement**: A Malicious insider (rogue TAM) Malicious TAM or external attacker with any valid TAM IAM credentials can call POST /reports/{id}/approve with valid credentials to approve reports for customers outside the TAM's responsibility scope, which leads to customer receives AI-generated report not reviewed by assigned TAM; quality and accuracy assurance bypassed

- **Prerequisites**: Malicious TAM or external attacker with any valid TAM IAM credentials
- **Action**: call POST /reports/{id}/approve with valid credentials to approve reports for customers outside the TAM's responsibility scope
- **Impact**: customer receives AI-generated report not reviewed by assigned TAM; quality and accuracy assurance bypassed
- **Impacted Assets**: A013
- **Tags**: human-review, approval, bypass

## Mitigations

### Identified Mitigations

#### M1: Reduce presigned URL expiry to 15 minutes for customer report sharing

#### M2: Add account ID allowlist check before STS AssumeRole to prevent cross-account access to unregistered accounts

#### M3: Enable AWS CloudTrail data events on S3 bucket and DynamoDB tables for tamper detection

#### M4: Add CloudWatch alarm on Lambda error rate and Bedrock throttle errors for DoS detection

#### M23: Reduce presigned S3 URL expiry to 15 minutes for customer report sharing

**Addresses Threats**: T10

#### M24: Add explicit account ID allowlist check before STS AssumeRole

**Addresses Threats**: T2, T16

#### M25: Enable CloudTrail data events on S3 and DynamoDB for tamper detection

**Addresses Threats**: T4, T8, T9

#### M26: Add CloudWatch alarms for Lambda error rate and Bedrock throttle errors

**Addresses Threats**: T13

### Resolved Mitigations

#### M5: IAM SigV4 authentication on all API Gateway endpoints

**Addresses Threats**: T1, T3, T17

#### M6: API Gateway throttling: 10 req/s sustained, 20 burst limit

**Addresses Threats**: T1, T13, T14

#### M7: Prompt injection detection via regex pattern matching in input_validator.py

**Addresses Threats**: T5, T11

#### M8: Amazon Bedrock Guardrails for content filtering and PII redaction on all LLM calls

**Addresses Threats**: T5, T11

#### M9: 5-layer guardrail pipeline applied to every assessment output

**Addresses Threats**: T5

#### M10: Human-in-the-loop review: customer reports require explicit TAM approval before delivery

**Addresses Threats**: T3, T7, T17

#### M11: Least-privilege IAM role scoped to specific resource ARNs

**Addresses Threats**: T1, T4, T6, T9, T12, T15

#### M12: Cross-account STS AssumeRole restricted to MIOAgentReadOnly role pattern only

**Addresses Threats**: T2, T16

#### M13: DynamoDB PITR enabled on all 4 tables for tampered data recovery

**Addresses Threats**: T4, T7, T9

#### M14: S3 bucket: block public access, SSL enforcement, AES256, versioning, 365-day lifecycle

**Addresses Threats**: T4, T8, T9

#### M15: SQS SQS-managed encryption and DLQ after 3 failed receive attempts

**Addresses Threats**: T6

#### M16: Lambda reserved concurrency limit of 10 on Coordinator to prevent resource exhaustion

**Addresses Threats**: T13, T14

#### M17: Structured JSON logging with CloudWatch Logs 30-day retention on all Lambda functions

**Addresses Threats**: T3, T7, T8, T12

#### M18: Presigned S3 URL expiry capped at 1 hour default

**Addresses Threats**: T10

#### M19: Input size limits: IaC templates 5MB, trigger_context 20 keys, account names 256 chars

#### M20: Confidence gate blocks customer output when assessment confidence is LOW

#### M21: Account registration allowlist: only registered accounts in mio-agent-accounts table can be assessed

**Addresses Threats**: T2

#### M22: Dependency pinning in requirements.txt to mitigate supply chain version confusion

**Addresses Threats**: T15

## Assumptions

### A001: Authentication

**Description**: IAM SigV4 enforced on all API Gateway endpoints — verified in infrastructure/stacks/mio_agent_stack.py _create_api_gateway() with authorization_type=apigw.AuthorizationType.IAM on every method.

- **Impact**: Spoofing threat a05f is substantially mitigated at the API boundary.
- **Rationale**: Code inspection confirmed: all 6 API methods (POST /assess, GET /assess/{id}, GET /accounts, GET /accounts/{id}/history, POST /reports/{id}/approve, POST /feedback) have explicit IAM auth.

### A002: Input Validation

**Description**: Prompt injection detection implemented in src/mio_agent/guardrails/input_validator.py with 9 regex patterns. sanitize_narrative_input() called before all Bedrock invocations in narrative.py.

- **Impact**: Prompt injection threat eb40 is partially mitigated. Regex patterns cover known injection phrases but cannot guarantee coverage of novel jailbreak patterns.
- **Rationale**: Code inspection confirmed: _PROMPT_INJECTION_PATTERNS list with 9 patterns, _check_prompt_injection() applied on account_name/requested_by/template_content, sanitize_narrative_input() in narrative generation path.

### A003: AWS Services

**Description**: STS AssumeRole in CDK restricted to arn:aws:iam::*:role/MIOAgentReadOnly only. However, no runtime check in Coordinator Lambda verifies the target account_id is in the registered accounts table before assuming the role.

- **Impact**: Threat a9ff (EoP via unregistered account cross-account access) is partially open. Account registration in mio-agent-accounts acts as a soft control but is not enforced at the STS call site.
- **Rationale**: Verified in orchestrator.py: role_arn comes from AssessmentRequest.role_arn which is caller-supplied. get_accounts_list() is called by scheduler but on-demand API accepts any caller-provided role_arn.

### A004: Authentication

**Description**: Human review gate (Layer 5) creates PENDING_REVIEW records for CUSTOMER and LEADERSHIP reports. TAM reviewed_by field is taken from request body (body.get reviewed_by), not derived from the authenticated IAM principal.

- **Impact**: Spoofing threat 30a4 (TAM alias impersonation in review approval) is not fully mitigated. The system trusts the caller to provide their own alias rather than extracting it from the IAM context.
- **Rationale**: Verified in api_handler.py _handle_approve_report(): reviewed_by = body.get(reviewed_by, unknown-tam). The IAM identity of the caller is not used to set reviewed_by.

## Phase Progress

| Phase | Name | Completion |
|---|---|---|
| 1 | Business Context Analysis | 100% ✅ |
| 2 | Architecture Analysis | 100% ✅ |
| 3 | Threat Actor Analysis | 100% ✅ |
| 4 | Trust Boundary Analysis | 100% ✅ |
| 5 | Asset Flow Analysis | 100% ✅ |
| 6 | Threat Identification | 100% ✅ |
| 7 | Mitigation Planning | 100% ✅ |
| 7.5 | Code Validation Analysis | 100% ✅ |
| 8 | Residual Risk Analysis | 100% ✅ |
| 9 | Output Generation and Documentation | 100% ✅ |

---

*This threat model report was generated automatically by the Threat Modeling MCP Server.*
