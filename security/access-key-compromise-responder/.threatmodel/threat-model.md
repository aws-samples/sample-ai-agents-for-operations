# Comprehensive Threat Model Report

**Generated**: 2026-09-18 11:26:39
**Current Phase**: 1 - Business Context Analysis
**Overall Completion**: 50.0%

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Context](#business-context)
3. [Classification Profiles](#classification-profiles)
4. [System Architecture](#system-architecture)
5. [Threat Actors](#threat-actors)
6. [Trust Boundaries](#trust-boundaries)
7. [Assets and Flows](#assets-and-flows)
8. [Threats](#threats)
9. [Mitigations](#mitigations)
10. [Assumptions](#assumptions)
11. [Phase Progress](#phase-progress)

## Executive Summary

Advisory, read-only serverless pipeline that detects exposed/compromised AWS access keys from AWS Health, Security Hub (GuardDuty), and Trusted Advisor, then invokes AWS DevOps Agent (via an HMAC-signed Agent Space webhook) to investigate 90 days of CloudTrail activity and produce human-run containment recommendations. It sends heads-up notifications (SNS email, optional Slack/Teams). It never mutates customer resources.

### Key Statistics

- **Total Threats**: 12
- **Total Mitigations**: 11
- **Total Assumptions**: 0
- **System Components**: 10
- **Assets**: 5
- **Threat Actors**: 0

## Business Context

**Description**: Advisory, read-only serverless pipeline that detects exposed/compromised AWS access keys from AWS Health, Security Hub (GuardDuty), and Trusted Advisor, then invokes AWS DevOps Agent (via an HMAC-signed Agent Space webhook) to investigate 90 days of CloudTrail activity and produce human-run containment recommendations. It sends heads-up notifications (SNS email, optional Slack/Teams). It never mutates customer resources.

### Business Features

- **Industry Sector**: Technology
- **Data Sensitivity**: Confidential
- **User Base Size**: Nano
- **Geographic Scope**: Multi-Continental / International
- **Regulatory Requirements**: None
- **System Criticality**: High
- **Financial Impact**: Moderate
- **Authentication Requirement**: MFA
- **Deployment Model**: Serverless / FaaS
- **User Base Metric**: Seats / licenses
- **Revenue Band**: Enterprise
- **Data Residency**: National / Single-Country
- **Compute Location**: National / Single-Country
- **User Base Location**: Multi-Continental / International
- **Organizational HQ**: Multi-Continental / International

## Classification Profiles

### Software Profile

- **Software Type**: Serverless Function
- **Deployment Model**: Serverless / FaaS
- **Architecture Style**: Event-driven
- **Platform / Runtime**: Cloud runtime
- **User Domain**: Security
- **Licensing / Ownership**: Open source
- **Modern Paradigms**: Cloud-native, Event-driven architecture, Agentic AI
- **Description**: AWS SAM stack: 3 EventBridge rules -> EventRouter Lambda -> Step Functions -> Callback Lambda (HMAC-signed HTTPS webhook to AWS DevOps Agent Space) -> Notifier Lambda (SNS + optional Slack/Teams). CMK-encrypted Secrets Manager secrets and SQS DLQ. Python 3.12 on ARM64. Read-only IAM only.

### Data Asset Profiles

| ID | Name | Asset | Category | Content Types | Sensitivity | Compliance | States | Volume | Lifecycle | Business Domain | Description |
|---|---|---|---|---|---|---|---|---|---|---|---|
| DP001 | DevOps Agent webhook HMAC secret | N/A | Secrets and Credentials | Authentication credentials | Restricted | N/A | At rest, In use | N/A | Active | Engineering | HMAC signing secret for the DevOps Agent Space webhook, stored in Secrets Manager encrypted with a customer-managed KMS key. |
| DP002 | Notification channel webhook URLs | N/A | Secrets and Credentials | Authentication credentials | Restricted | N/A | At rest, In use | N/A | Active | Engineering | Slack/Teams incoming webhook URLs (bearer-style secrets in the URL path) stored in Secrets Manager, CMK-encrypted. |
| DP003 | Compromise incident + investigation report | N/A | Semi-Structured Data | Operational / telemetry data, System metadata | Confidential | N/A | In transit, In use | N/A | Active | Operations | Normalized incident (exposed access key ID, principal, account, detection source) plus the agent's Markdown investigation report with CloudTrail activity, affected resources, and containment recommendations. |
| DP004 | Account context (alternate contacts, tags) | N/A | Structured Data | PII, System metadata | Confidential | N/A | In transit, In use | N/A | Active | Operations | Account name, email, Organizations tags, and Operations/Security/Billing alternate-contact names, emails, and phone numbers resolved read-only for notification routing. |

### User Personas

| ID | Persona | Name | Privilege | Affiliation | Roles | Intent | Entity Type | Authentication | Threat Actor Overlay | In Scope | Description |
|---|---|---|---|---|---|---|---|---|---|---|---|
| UP001 | External Service / Third-Party Integration | AWS detection sources (EventBridge) | Low | Vendor | Integration Endpoint | Legitimate | External System | IAM Role | N/A | Yes | AWS Health, Security Hub (GuardDuty), and Trusted Advisor emit events onto the default event bus that match the 3 EventBridge rules and trigger the EventRouter Lambda. |
| UP002 | Non-Human Identity / Service Account | Pipeline Lambda execution roles | Low | Employee | Service Account | Legitimate | Non-Human | IAM Role | N/A | Yes | EventRouter, Callback, and Notifier Lambda roles. Read-only on customer resources; scoped write only to their own SNS topic, Secrets Manager secrets (read), and Step Functions start. |
| UP003 | External Service / Third-Party Integration | AWS DevOps Agent Space (webhook receiver) | Medium | Vendor | Integration Endpoint | Legitimate | External System | Token | N/A | Yes | Managed AWS DevOps Agent that receives the HMAC-signed webhook, runs the investigation skill (read-only), and surfaces findings in its operator app. |
| UP004 | Support / Operator User | Security responder | Elevated | Employee | Operator, Support | Legitimate | Human | Multi-factor | N/A | Yes | Human engineer who receives the notification, reviews findings in the DevOps Agent operator app, and manually runs the recommended containment steps. |
| UP005 | External Service / Third-Party Integration | Slack / Microsoft Teams | None | Vendor | Integration Endpoint | Legitimate | External System | API Key | N/A | Yes | Optional chat notification sinks that receive the heads-up message via incoming webhook URLs. |

### Non-Functional Requirements

- **Time Behaviour**: Responsive — Detection-to-investigation trigger completes in seconds; investigation itself runs asynchronously in the managed agent.
- **Availability**: 99.9% — Serverless (Lambda, Step Functions, EventBridge) with an SQS DLQ capturing failed invocations for redrive.
- **Recoverability**: Minutes — Failed events land in a 14-day-retention SQS DLQ for manual redrive; stack is redeployable via SAM.
- **Fail-Safe Behaviour**: Revert to safe state — Advisory-only by design: on any failure or ambiguity the pipeline notifies humans and takes no mutating action.
- **Safety Criticality**: Safety-related — Feeds incident-response decisions for compromised credentials; incorrect or missing findings could delay containment.
- **Faultlessness**: High — Regex/Markdown parsing of the agent output contract and event parsing must degrade gracefully rather than crash the pipeline.

## System Architecture

### Components

| ID | Name | Type | Service Provider | Description |
|---|---|---|---|---|
| C001 | EventBridge Detection Rules | Serverless | AWS | Three rules matching aws.health (AWS_RISK_CREDENTIALS_EXPOSED), aws.securityhub (AwsIamAccessKey ACTIVE/NEW findings), and aws.trustedadvisor (Exposed Access Keys) on the default event bus; targets the EventRouter Lambda. |
| C002 | EventRouter Lambda | Serverless | AWS | Python 3.12/ARM64. Parses the source event into a normalized incident, enriches with read-only account context (Organizations + Account alternate contacts), and starts the Step Functions execution. Read-only w.r.t. customer resources. |
| C003 | Investigation State Machine | Serverless | AWS | Two-state workflow: InvokeAgent (Callback Lambda) then HeadsUpNotify (Notifier Lambda). |
| C004 | Callback Lambda | Serverless | AWS | Invokes the AWS DevOps Agent Space webhook over HMAC-signed HTTPS to start an investigation, passing the incident and callback task token. Also parses the agent's Markdown report on callback. HTTPS-only enforced. |
| C005 | Notifier Lambda | Serverless | AWS | Publishes heads-up and findings notifications to SNS (email) and optional Slack/Teams incoming webhooks (HTTPS-only). Falls back to SNS if chat delivery fails. |
| C006 | AWS DevOps Agent Space | Other | AWS | Managed agent that receives the HMAC-signed webhook, runs the access-key-compromise investigation skill (read-only CloudTrail/IAM/resource describe), and surfaces findings + human-run recommendations in its operator app. |
| C007 | SNS Alerts Topic | Messaging | AWS | Email-subscribed topic used as the reliable notification fallback. Encrypted with the aws/sns managed key. |
| C008 | KMS CMK | Security | AWS | Customer-managed key with rotation enabled; encrypts the two Secrets Manager secrets and the SQS DLQ. Key policy grants secretsmanager/sqs/lambda service principals Decrypt+GenerateDataKey. |
| C009 | Slack/Teams Webhooks | Network | Other | Optional external chat sinks reached over HTTPS using bearer-style webhook URLs held in Secrets Manager. |
| C010 | Security Responder (operator) | Other | Other | Human engineer who reviews findings in the DevOps Agent operator app and manually runs containment steps. All mutation is human-performed, outside this system. |

### Connections

| ID | Source | Destination | Protocol | Port | Encrypted | Description |
|---|---|---|---|---|---|---|
| CN001 | EventBridge Detection Rules (C001) | EventRouter Lambda (C002) | HTTPS | N/A | Yes | EventBridge invokes EventRouter Lambda on a matching detection event (async, AWS-internal). |
| CN002 | EventRouter Lambda (C002) | Investigation State Machine (C003) | HTTPS | N/A | Yes | EventRouter calls states:StartExecution with the normalized incident. |
| CN003 | EventRouter Lambda (C002) | Investigation State Machine (C003) | HTTPS | N/A | Yes | EventRouter reads read-only account context via Organizations DescribeAccount/ListTagsForResource and Account GetAlternateContact. |
| CN004 | Investigation State Machine (C003) | Callback Lambda (C004) | HTTPS | N/A | Yes | State machine invokes Callback Lambda (InvokeAgent state). |
| CN005 | Investigation State Machine (C003) | Notifier Lambda (C005) | HTTPS | N/A | Yes | State machine invokes Notifier Lambda (HeadsUpNotify state). |
| CN006 | Callback Lambda (C004) | WebhookSecret (Secrets Manager) (D001) | HTTPS | N/A | Yes | Callback reads the HMAC secret from Secrets Manager (GetSecretValue). |
| CN007 | Callback Lambda (C004) | AWS DevOps Agent Space (C006) | HTTPS | 443 | Yes | Callback POSTs the HMAC-signed incident to the DevOps Agent Space webhook (external HTTPS egress). |
| CN008 | Notifier Lambda (C005) | Channels Secret (Secrets Manager) (D002) | HTTPS | N/A | Yes | Notifier reads Slack/Teams webhook URLs from Secrets Manager (GetSecretValue). |
| CN009 | Notifier Lambda (C005) | SNS Alerts Topic (C007) | HTTPS | N/A | Yes | Notifier publishes to the SNS Alerts topic (sns:Publish). |
| CN010 | Notifier Lambda (C005) | Slack/Teams Webhooks (C009) | HTTPS | 443 | Yes | Notifier POSTs the heads-up message to optional Slack/Teams webhooks (external HTTPS egress). |
| CN011 | SNS Alerts Topic (C007) | Security Responder (operator) (C010) | SMTP | N/A | Yes | SNS delivers the email notification to the subscribed security responder. |
| CN012 | AWS DevOps Agent Space (C006) | Security Responder (operator) (C010) | HTTPS | N/A | Yes | Responder reviews findings/recommendations in the DevOps Agent operator app and manually runs containment. |
| CN013 | EventRouter Lambda (C002) | Dead Letter Queue (SQS) (D003) | HTTPS | N/A | Yes | Failed async invocations of the Lambdas are routed to the SQS DLQ. |
| CN014 | KMS CMK (C008) | WebhookSecret (Secrets Manager) (D001) | HTTPS | N/A | Yes | KMS CMK encrypts/decrypts the WebhookSecret. |
| CN015 | KMS CMK (C008) | Channels Secret (Secrets Manager) (D002) | HTTPS | N/A | Yes | KMS CMK encrypts/decrypts the Channels secret. |
| CN016 | KMS CMK (C008) | Dead Letter Queue (SQS) (D003) | HTTPS | N/A | Yes | KMS CMK encrypts the SQS DLQ messages. |

### Data Stores

| ID | Name | Type | Classification | Encrypted at Rest | Description |
|---|---|---|---|---|---|
| D001 | WebhookSecret (Secrets Manager) | Other | Restricted | Yes | HMAC signing secret for the DevOps Agent webhook. CMK-encrypted. NoEcho parameter at deploy time. |
| D002 | Channels Secret (Secrets Manager) | Other | Restricted | Yes | JSON of Slack/Teams webhook URLs (bearer-style secrets). CMK-encrypted. |
| D003 | Dead Letter Queue (SQS) | Other | Confidential | Yes | Captures failed async Lambda invocations for 14 days for manual redrive. CMK-encrypted. Holds incident payloads (access key IDs, account context). |
| D004 | CloudWatch Logs | Other | Confidential | No | Structured logs from the three Lambdas. May contain incident fields (access key IDs, account IDs); code truncates payloads and avoids logging full webhook URLs. |

## Threat Actors

*No threat actors reviewed for this system.*

## Trust Boundaries

### Trust Zones

#### AWS Event Sources

- **Trust Level**: Medium
- **Description**: AWS Health, Security Hub/GuardDuty, and Trusted Advisor emitting onto the default EventBridge bus. AWS-managed but the event payload content is attacker-influenceable (e.g., exposure location, principal fields).

#### Pipeline Account (deployed stack)

- **Trust Level**: High
- **Description**: The customer AWS account boundary containing the SAM stack: EventBridge rules, the three Lambdas, Step Functions, SNS, KMS CMK, Secrets Manager secrets, and the SQS DLQ.

#### AWS DevOps Agent Space

- **Trust Level**: High
- **Description**: Managed AWS DevOps Agent service that receives the webhook and runs the read-only investigation skill.

#### External Chat Providers

- **Trust Level**: Untrusted
- **Description**: Slack and Microsoft Teams - third-party SaaS reached over the public internet via incoming webhook URLs.

#### Human Responder

- **Trust Level**: High
- **Description**: The security engineer who reviews findings and manually performs any containment. Outside the automated system.

## Assets and Flows

### Assets

| ID | Name | Type | Classification | Lifecycle | Data States | Criticality | Owner |
|---|---|---|---|---|---|---|---|
| A001 | Compromise incident payload | Data | Confidential | Active | In transit, In use | 4 | Pipeline |
| A002 | Account alternate contacts (PII) | Data | Confidential | Active | In transit, In use | 3 | Pipeline |
| A003 | Webhook HMAC secret | Credential | Restricted | Active | At rest, In use | 5 | Pipeline |
| A004 | Chat webhook URLs | Credential | Restricted | Active | At rest, In use | 4 | Pipeline |
| A005 | Investigation report | Data | Confidential | Active | In transit, In use | 4 | DevOps Agent |

### Asset Flows

| ID | Asset | Source | Destination | Protocol | Encrypted | Risk Level |
|---|---|---|---|---|---|---|
| F001 | Compromise incident payload | EventBridge Detection Rules (C001) | EventRouter Lambda (C002) | HTTPS | Yes | 3 |
| F002 | Account alternate contacts (PII) | EventRouter Lambda (C002) | Investigation State Machine (C003) | HTTPS | Yes | 2 |
| F003 | Webhook HMAC secret | WebhookSecret (Secrets Manager) (D001) | Callback Lambda (C004) | HTTPS | Yes | 3 |
| F004 | Compromise incident payload | Callback Lambda (C004) | AWS DevOps Agent Space (C006) | HTTPS | Yes | 3 |
| F005 | Chat webhook URLs | Channels Secret (Secrets Manager) (D002) | Notifier Lambda (C005) | HTTPS | Yes | 3 |
| F006 | Investigation report | Notifier Lambda (C005) | Slack/Teams Webhooks (C009) | HTTPS | Yes | 3 |
| F007 | Investigation report | Notifier Lambda (C005) | SNS Alerts Topic (C007) | HTTPS | Yes | 2 |
| F008 | Account alternate contacts (PII) | SNS Alerts Topic (C007) | Security Responder (operator) (C010) | SMTP | Yes | 2 |
| F009 | Compromise incident payload | EventRouter Lambda (C002) | Dead Letter Queue (SQS) (D003) | HTTPS | Yes | 2 |

## Threats

### Identified Threats

#### T1: An attacker able to reach the DevOps Agent Space webhook endpoint

**Statement**: A An attacker able to reach the DevOps Agent Space webhook endpoint Knowledge of the webhook URL but not the HMAC secret, or a weak/empty secret can forges a webhook POST or replays a captured request to impersonate the pipeline and start bogus investigations, which leads to Spurious investigations, responder alert fatigue, and potential masking of a real compromise

- **Prerequisites**: Knowledge of the webhook URL but not the HMAC secret, or a weak/empty secret
- **Action**: forges a webhook POST or replays a captured request to impersonate the pipeline and start bogus investigations
- **Impact**: Spurious investigations, responder alert fatigue, and potential masking of a real compromise
- **Impacted Assets**: A003, A001
- **Tags**: webhook, hmac

#### T2: An identity in the account with events:PutEvents or a misconfigured cross-account rule

**Statement**: A An identity in the account with events:PutEvents or a misconfigured cross-account rule Ability to put a custom event matching a rule pattern onto the default bus (note: aws.health/securityhub/trustedadvisor are reserved sources and cannot be spoofed directly) can injects a crafted manual/custom event to trigger investigations against arbitrary keys/accounts, which leads to Attacker-directed investigations and notification spam; possible reconnaissance of account context

- **Prerequisites**: Ability to put a custom event matching a rule pattern onto the default bus (note: aws.health/securityhub/trustedadvisor are reserved sources and cannot be spoofed directly)
- **Action**: injects a crafted manual/custom event to trigger investigations against arbitrary keys/accounts
- **Impact**: Attacker-directed investigations and notification spam; possible reconnaissance of account context
- **Impacted Assets**: A001
- **Tags**: eventbridge, input

#### T3: A malicious or compromised DevOps Agent Space, or a MITM on the report path

**Statement**: A A malicious or compromised DevOps Agent Space, or a MITM on the report path Ability to control the agent's Markdown output or the callback content can returns a manipulated investigation report (prompt-injection via CloudTrail-sourced strings, or forged findings) that the Callback Lambda parses with regex, which leads to Responder acts on false findings/recommendations; missed real resources; parser produces incomplete data silently

- **Prerequisites**: Ability to control the agent's Markdown output or the callback content
- **Action**: returns a manipulated investigation report (prompt-injection via CloudTrail-sourced strings, or forged findings) that the Callback Lambda parses with regex
- **Impact**: Responder acts on false findings/recommendations; missed real resources; parser produces incomplete data silently
- **Impacted Assets**: A005
- **Tags**: prompt-injection, parsing, output-validation

#### T4: An attacker who can influence CloudTrail-recorded strings the agent reads

**Statement**: A An attacker who can influence CloudTrail-recorded strings the agent reads Ability to seed user-agent/resource names/paths that the investigation surfaces into the report can embeds injection payloads or misleading text into fields the agent renders into its report or the notification body, which leads to Misleading notifications, possible rendering issues in Slack/Teams, responder misdirection

- **Prerequisites**: Ability to seed user-agent/resource names/paths that the investigation surfaces into the report
- **Action**: embeds injection payloads or misleading text into fields the agent renders into its report or the notification body
- **Impact**: Misleading notifications, possible rendering issues in Slack/Teams, responder misdirection
- **Impacted Assets**: A005
- **Tags**: prompt-injection, log-injection

#### T5: An operator or attacker performing actions with insufficient audit trail

**Statement**: A An operator or attacker performing actions with insufficient audit trail Gaps in logging or unencrypted/tamperable logs can performs or triggers pipeline actions (or manual containment) that cannot be reliably attributed after the fact, which leads to Incomplete incident forensics; disputes over who did what during response

- **Prerequisites**: Gaps in logging or unencrypted/tamperable logs
- **Action**: performs or triggers pipeline actions (or manual containment) that cannot be reliably attributed after the fact
- **Impact**: Incomplete incident forensics; disputes over who did what during response
- **Impacted Assets**: A001
- **Tags**: audit, logging

#### T6: An attacker or unauthorized user with access to notification sinks or logs

**Statement**: A An attacker or unauthorized user with access to notification sinks or logs Access to a misdirected SNS subscription, a leaked Slack/Teams webhook, or CloudWatch Logs can reads incident details and account alternate-contact PII (names, emails, phone numbers) carried in notifications and logs, which leads to Disclosure of PII and security-incident detail; aids social engineering of account owners

- **Prerequisites**: Access to a misdirected SNS subscription, a leaked Slack/Teams webhook, or CloudWatch Logs
- **Action**: reads incident details and account alternate-contact PII (names, emails, phone numbers) carried in notifications and logs
- **Impact**: Disclosure of PII and security-incident detail; aids social engineering of account owners
- **Impacted Assets**: A002, A005
- **Tags**: pii, notifications, logs

#### T7: An attacker who compromises a Lambda role or reads the CloudFormation/SAM parameters

**Statement**: A An attacker who compromises a Lambda role or reads the CloudFormation/SAM parameters Access to the Lambda execution role, the KMS CMK grants, or the deploy-time WebhookSecret parameter can retrieves the HMAC secret or chat webhook URLs from Secrets Manager or template parameters, which leads to Enables webhook spoofing and notification hijacking; loss of channel integrity

- **Prerequisites**: Access to the Lambda execution role, the KMS CMK grants, or the deploy-time WebhookSecret parameter
- **Action**: retrieves the HMAC secret or chat webhook URLs from Secrets Manager or template parameters
- **Impact**: Enables webhook spoofing and notification hijacking; loss of channel integrity
- **Impacted Assets**: A003, A004
- **Tags**: secrets, kms

#### T8: An attacker able to generate detection events or event floods

**Statement**: A An attacker able to generate detection events or event floods Ability to cause many matching events (e.g., mass key exposures) exceeding reserved concurrency (5) per function can floods the pipeline so investigations queue, throttle, or spill to the DLQ, which leads to Delayed or dropped investigations of real compromises; responder overload

- **Prerequisites**: Ability to cause many matching events (e.g., mass key exposures) exceeding reserved concurrency (5) per function
- **Action**: floods the pipeline so investigations queue, throttle, or spill to the DLQ
- **Impact**: Delayed or dropped investigations of real compromises; responder overload
- **Impacted Assets**: A001
- **Tags**: dos, concurrency

#### T9: An unreachable or slow DevOps Agent webhook / chat provider

**Statement**: A An unreachable or slow DevOps Agent webhook / chat provider Webhook endpoint down, misconfigured, or rate-limiting can causes Callback/Notifier invocations to fail; without retries a single transient failure drops the investigation, which leads to Investigation never starts or notification never delivered for a real compromise

- **Prerequisites**: Webhook endpoint down, misconfigured, or rate-limiting
- **Action**: causes Callback/Notifier invocations to fail; without retries a single transient failure drops the investigation
- **Impact**: Investigation never starts or notification never delivered for a real compromise
- **Impacted Assets**: A001, A005
- **Tags**: availability, retries

#### T10: An attacker who compromises the EventRouter Lambda role

**Statement**: A An attacker who compromises the EventRouter Lambda role Code execution in EventRouter or theft of its role credentials can abuses the broad Resource:* on organizations:DescribeAccount/ListTagsForResource and account:GetAlternateContact to enumerate the whole AWS Organization, which leads to Organization-wide reconnaissance: account names, emails, tags, and contact PII across all accounts

- **Prerequisites**: Code execution in EventRouter or theft of its role credentials
- **Action**: abuses the broad Resource:* on organizations:DescribeAccount/ListTagsForResource and account:GetAlternateContact to enumerate the whole AWS Organization
- **Impact**: Organization-wide reconnaissance: account names, emails, tags, and contact PII across all accounts
- **Impacted Assets**: A002
- **Tags**: iam, wildcard-resource, least-privilege

#### T11: A principal able to modify the SAM stack, Lambda code, or KMS key policy

**Statement**: A A principal able to modify the SAM stack, Lambda code, or KMS key policy Write access to the pipeline account's IaC, functions, or the CMK policy can alters the pipeline to exfiltrate secrets, redirect notifications, or (if the specced auto-remediation is ever added) gain mutating permissions, which leads to Full compromise of the response pipeline; secret theft; notification hijack

- **Prerequisites**: Write access to the pipeline account's IaC, functions, or the CMK policy
- **Action**: alters the pipeline to exfiltrate secrets, redirect notifications, or (if the specced auto-remediation is ever added) gain mutating permissions
- **Impact**: Full compromise of the response pipeline; secret theft; notification hijack
- **Impacted Assets**: A003, A004
- **Tags**: supply-chain, iac, kms-policy

#### T12: A local operator running the raw WebhookSecret through the shell at deploy time

**Statement**: A A local operator running the raw WebhookSecret through the shell at deploy time Passing WebhookSecret on the CLI/env during sam deploy can leaks the secret via shell history or process listing despite NoEcho on the CFN parameter, which leads to Secret disclosure enabling webhook spoofing

- **Prerequisites**: Passing WebhookSecret on the CLI/env during sam deploy
- **Action**: leaks the secret via shell history or process listing despite NoEcho on the CFN parameter
- **Impact**: Secret disclosure enabling webhook spoofing
- **Impacted Assets**: A003
- **Tags**: deploy, secrets-hygiene

## Mitigations

### Identified Mitigations

#### M1: HMAC-SHA256 request signing on the webhook with a strong secret; enforce timestamp/nonce freshness to prevent replay

**Addresses Threats**: T1

#### M2: Scope EventBridge to reserved AWS sources and validate/normalize event fields before use

**Addresses Threats**: T2

#### M3: Validate and sanitize the agent's Markdown report against the output contract; encode output before rendering

**Addresses Threats**: T3, T4

#### M4: Comprehensive audit logging: CloudTrail, Step Functions history, structured Lambda logs with failure context

**Addresses Threats**: T5, T6, T10

#### M5: Minimize and protect PII in notifications and logs; encrypt SNS; restrict subscribers and log access

**Addresses Threats**: T4, T6

#### M6: Store secrets in Secrets Manager with a customer-managed KMS CMK; scope GetSecretValue and kms:Decrypt to specific ARNs

**Addresses Threats**: T7, T11, T12

#### M7: Reserved concurrency + SQS DLQ with redrive; alarm on DLQ depth and throttles

**Addresses Threats**: T8

#### M8: Graceful failure handling with SNS fallback plus bounded retry/backoff and delivery-failure alarms

**Addresses Threats**: T9

#### M9: Accept unavoidable Resource:* on Organizations/Account read APIs; compensate with read-only role, monitoring, and anomaly alarms

**Addresses Threats**: T10

#### M10: Advisory-only design: no auto-mutation; human-in-the-loop performs all containment

**Addresses Threats**: T1, T2, T3

#### M11: Protect IaC/code/KMS policy with least-privilege deploy roles, review, and change monitoring; avoid CLI secret exposure

**Addresses Threats**: T7, T11, T12

## Assumptions

*No assumptions defined.*

## Phase Progress

| Phase | Name | Completion |
|---|---|---|
| 1 | Business Context Analysis | 100% ✅ |
| 2 | Architecture Analysis | 0% ⏳ |
| 3 | Threat Actor Analysis | 0% ⏳ |
| 4 | Trust Boundary Analysis | 0% ⏳ |
| 5 | Asset Flow Analysis | 100% ✅ |
| 6 | Threat Identification | 100% ✅ |
| 7 | Mitigation Planning | 100% ✅ |
| 7.5 | Code Validation Analysis | 0% ⏳ |
| 8 | Residual Risk Analysis | 0% ⏳ |
| 9 | Output Generation and Documentation | 100% ✅ |

---

*This threat model report was generated automatically by the Threat Modeling MCP Server.*
