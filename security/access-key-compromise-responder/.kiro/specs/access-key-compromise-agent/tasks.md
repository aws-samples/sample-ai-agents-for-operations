# Implementation Plan: Access Key Compromise Response Agent

## Overview

This implementation plan covers the full build-out of the AWS Access Key Compromise Response Agent — a two-agent system deployed on Amazon Bedrock AgentCore that automatically detects, investigates, and remediates compromised AWS access keys. The system is event-driven, responding to alerts from AWS Health, Security Hub (GuardDuty), and Trusted Advisor. Tasks are ordered to build foundational components first, then layer detection, analysis, remediation, notifications, and infrastructure on top.

## Tasks

- [ ] 1. Set up project structure, data models, and configuration
  - [ ] 1.1 Create project directory structure and package scaffolding
    - Create `src/AccessKeyCompromiseAgent/` with subdirectories: `agent/`, `aws/`, `models/`, `config/`, `tests/`, `tests/unit/`
    - Create `__init__.py` files for all packages
    - Create `requirements.txt` with dependencies: boto3, pyyaml, requests, pytest, hypothesis
    - Create `setup.py` and `pytest.ini`
    - Create `Makefile` with targets: test, lint, deploy
    - _Requirements: 23.1, 24.1_

  - [ ] 1.2 Implement data models for alerts and investigations
    - Create `models/alert.py` with `CompromiseAlert` dataclass and `AlertSource` enum (TRUSTED_ADVISOR, SECURITY_HUB, HEALTH_DASHBOARD, MANUAL)
    - Create `models/investigation.py` with: `InvestigationReport`, `APICall`, `ResourceActivity`, `IPAddress`, `BehavioralAnomaly`, `ResourceNode`, `AttackChain`, `HistoricalBaseline`, `PostCompromiseActivity`, `AccountType` enum, `ResourceType` enum (30+ values)
    - Include validation in `CompromiseAlert.__post_init__` and serialization via `to_dict()`
    - _Requirements: 1.1, 1.2, 1.3, 3.4, 5.5, 6.1_

  - [ ] 1.3 Implement YAML-based configuration loading
    - Create `models/config.py` with `AgentConfig` dataclass
    - Implement `load_from_file()` and `load_default()` class methods
    - Support all config sections: analysis, remediation, alerts, paging_rules, organizations, features
    - Implement safe defaults when config file not found (dry-run enabled, remediation disabled, 7-day lookback, 90-day baseline)
    - Create `config/agent_config.yaml` with full configuration structure
    - _Requirements: 23.1, 23.2, 23.3, 23.4_

  - [ ]* 1.4 Write property test for configuration loading round-trip
    - **Property 25: Configuration Loading Round-Trip**
    - **Validates: Requirements 23.1, 23.2**

- [ ] 2. Implement AWS service client wrappers
  - [ ] 2.1 Create CloudTrail client with paginated event lookup
    - Create `aws/cloudtrail.py` with `CloudTrailClient` class
    - Implement `lookup_events()` with pagination support
    - Handle `LookupAttributes` for AccessKeyId and Username queries
    - _Requirements: 3.1, 3.2_

  - [ ] 2.2 Create IAM client for key management and policy operations
    - Create `aws/iam.py` with `IAMClient` class
    - Implement: `update_access_key()`, `list_access_keys()`, `attach_user_policy()`, `attach_role_policy()`, `put_user_policy()`, `put_role_policy()`
    - _Requirements: 7.5, 11.1, 11.2, 12.1_

  - [ ] 2.3 Create Security Hub, Health, and Trusted Advisor clients
    - Create `aws/security_hub.py` with `SecurityHubClient` — `get_findings()` with filters
    - Create `aws/health.py` with `HealthClient` — `get_access_key_events()`, `get_event_details()`, `get_affected_entities()`
    - Create `aws/trusted_advisor.py` with `TrustedAdvisorClient` — `get_check_results()` for check ID `12Fnkpl8Y5`
    - _Requirements: 1.1, 1.2, 1.3, 1.5_

  - [ ] 2.4 Create Organizations client with account classification and caching
    - Create `aws/organizations.py` with `OrganizationsConfigManager` class
    - Implement `is_production_account()` using Organizations tags
    - Support configurable tag key (default: "Environment") and value lists
    - Implement classification caching with configurable TTL (default: 5 minutes)
    - Default to PRODUCTION on API failure or unrecognized values
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [ ] 2.5 Create ResourceManager for EC2, S3, Lambda, RDS quarantine operations
    - Create `aws/resource_manager.py` with `ResourceManager` class
    - Implement: `stop_ec2_instance()`, `isolate_ec2_security_group()`, `block_s3_bucket_access()`, `add_s3_deny_policy()`, `remove_lambda_permissions()`, `disable_lambda_function()`, `stop_rds_instance()`, `isolate_rds_security_groups()`
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 14.1, 14.2, 15.1, 15.2, 15.3, 16.1, 16.2, 17.1, 17.2, 17.3_

  - [ ]* 2.6 Write property test for account classification
    - **Property 14: Account Classification**
    - **Validates: Requirements 8.2, 8.3, 8.4**

- [ ] 3. Implement multi-source compromise detection
  - [ ] 3.1 Implement CompromiseDetector with three detection sources
    - Create `agent/detector.py` with `CompromiseDetector` class
    - Implement `detect_all()` orchestrating all three sources
    - Implement `_detect_from_trusted_advisor()` — parse check ID `12Fnkpl8Y5` results
    - Implement `_detect_from_security_hub()` — filter by ResourceType=AwsIamAccessKey, RecordState=ACTIVE
    - Implement `_detect_from_health()` — query AWS_RISK_CREDENTIALS_EXPOSED events
    - Handle graceful degradation when sources are unavailable (SubscriptionRequiredException)
    - _Requirements: 1.1, 1.2, 1.3, 1.5_

  - [ ] 3.2 Implement alert deduplication and parsing
    - Implement `_deduplicate_alerts()` — deduplicate by access_key_id
    - Implement `_parse_ta_alert()`, `_parse_sh_alert()`, `_parse_health_alert()` — normalize to CompromiseAlert
    - Handle multiple Security Hub resource ID formats (ARN, AWS::IAM, direct)
    - _Requirements: 1.4_

  - [ ]* 3.3 Write property test for alert deduplication
    - **Property 2: Alert Deduplication by Access Key ID**
    - **Validates: Requirements 1.4**

  - [ ]* 3.4 Write unit tests for CompromiseDetector
    - Test each detection source parser with sample events
    - Test graceful degradation when sources are unavailable
    - Test deduplication with overlapping alerts
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Implement CloudTrail analysis and anomaly detection
  - [ ] 5.1 Implement CloudTrailAnalyzer with historical baseline
    - Create `agent/analyzer.py` with `CloudTrailAnalyzer` class
    - Implement `analyze()` orchestrating the full analysis pipeline
    - Implement `_query_cloudtrail()` with configurable lookback period
    - Implement `_build_historical_baseline()` — 90-day baseline with adaptive reduction
    - Implement `_parse_api_calls()` — parse CloudTrail events into structured APICall records
    - Define CREATION_EVENTS (40+), MODIFICATION_EVENTS (20+), DELETE_EVENTS (15+) sets
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ] 5.2 Implement post-compromise activity analysis
    - Implement `_analyze_post_compromise()` — filter events after compromise timestamp
    - Track: API call frequency, high-risk API calls, resources created, new regions, new IPs
    - Calculate anomaly scores for API calls, resource creation, and geographic patterns
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ] 5.3 Implement behavioral anomaly detection
    - Implement `_detect_anomalies_enhanced()` with 6 anomaly types:
      - new_api_calls (HIGH) — APIs not in baseline
      - new_ip_addresses (HIGH) — IPs not in baseline
      - new_regions (MEDIUM) — regions not in baseline
      - volume_spike (MEDIUM) — rate > 3x baseline daily average
      - high_risk_operations (CRITICAL) — Create/Delete/Put/Attach/Detach calls
      - resource_creation_spike (HIGH) — resource rate > 2x baseline daily average
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ] 5.4 Implement risk level calculation and cost impact estimation
    - Implement `_calculate_risk_level()` — score based on anomaly severity (CRITICAL=10, HIGH=5), attack chain depth, resource creation
    - Map scores to CRITICAL/HIGH/MEDIUM/LOW levels
    - Implement `_calculate_cost_impact()` — estimate USD cost of created resources
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ]* 5.5 Write property tests for anomaly detection
    - **Property 7: Set-Difference Anomaly Detection**
    - **Validates: Requirements 4.1, 4.2, 4.3**

  - [ ]* 5.6 Write property tests for rate threshold anomaly detection
    - **Property 8: Rate Threshold Anomaly Detection**
    - **Validates: Requirements 4.4, 4.6**

  - [ ]* 5.7 Write property test for high-risk operations detection
    - **Property 9: High-Risk Operations Anomaly Detection**
    - **Validates: Requirements 4.5**

  - [ ]* 5.8 Write property test for historical baseline computation
    - **Property 4: Historical Baseline Correctly Computes Statistics**
    - **Validates: Requirements 3.2, 3.3**

  - [ ]* 5.9 Write property test for risk level calculation
    - **Property 12: Risk Level Calculation**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.4**

- [ ] 6. Implement transitive resource discovery
  - [ ] 6.1 Implement TransitiveResourceDiscoverer with unlimited depth traversal
    - Create `agent/resource_discoverer.py` with `TransitiveResourceDiscoverer` class
    - Implement `discover()` — recursive BFS algorithm with timeout
    - Level 0: Extract resources from post-compromise events matching creation event names
    - For IAM principals (users/roles): recursively query CloudTrail for resources created by that principal
    - Continue until no new IAM principals found or configurable timeout reached (default: 10 minutes)
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ] 6.2 Implement resource extractors for 40+ AWS resource types
    - Implement `_discover_direct_resources()` with creation event mapping
    - Implement type-specific extractors: `_extract_ec2_instance()`, `_extract_iam_user()`, `_extract_iam_role()`, `_extract_lambda_function()`, `_extract_s3_bucket()`, `_extract_rds_instance()`, `_extract_ecs_cluster()`
    - Implement `_extract_generic()` fallback for remaining resource types
    - Build ResourceNode tree with: resource_id, resource_type, resource_arn, created_at, created_by, attack_chain_level, parent, children
    - _Requirements: 5.4, 5.5_

  - [ ]* 6.3 Write property test for resource discovery post-compromise filtering
    - **Property 10: Resource Discovery Captures Post-Compromise Creations**
    - **Validates: Requirements 5.1**

  - [ ]* 6.4 Write property test for attack chain node completeness
    - **Property 11: Attack Chain Node Completeness**
    - **Validates: Requirements 5.5**

- [ ] 7. Implement remediation layer
  - [ ] 7.1 Implement KeyRemediator with resource-type-specific quarantine handlers
    - Create `agent/remediator.py` with `KeyRemediator` class
    - Implement `remediate()` orchestrating: deactivate key → quarantine attack chain → update report
    - Implement handler dispatch by ResourceType enum
    - _Requirements: 9.3, 10.2, 25.1, 25.2_

  - [ ] 7.2 Implement IAM user and role quarantine handlers
    - Implement `_quarantine_iam_user()`: attach deny-all policy, deactivate all access keys, remove from groups
    - Implement `_quarantine_iam_role()`: attach deny-all policy
    - Record all actions taken on each resource
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 12.1, 12.2_

  - [ ] 7.3 Implement EC2, S3, Lambda, RDS, and ECS quarantine handlers
    - EC2: create isolated security group, attach to instance, detach IAM profile, stop instance
    - S3: apply deny-all bucket policy (except owning account), enable versioning, block public access
    - Lambda: set reserved concurrency to 0, remove resource-based permissions
    - RDS: create snapshot, create isolated security group, stop instance
    - ECS: stop task with quarantine reason
    - _Requirements: 13.1–13.5, 14.1–14.4, 15.1–15.4, 16.1–16.2, 17.1–17.4, 18.1–18.2_

  - [ ]* 7.4 Write property test for quarantine never deletes
    - **Property 26: Quarantine Never Deletes**
    - **Validates: Requirements 25.1, 25.2**

  - [ ]* 7.5 Write property test for quarantine actions recorded
    - **Property 18: Quarantine Actions Are Recorded**
    - **Validates: Requirements 11.4, 12.2**

- [ ] 8. Implement dynamic IAM policy generation
  - [ ] 8.1 Implement DynamicPolicyGenerator with resource-scoped policies
    - Create `agent/policy_generator.py` with `DynamicPolicyGenerator` class
    - Implement `generate_remediation_policy()` — extract resources from attack chain, group by type
    - Generate separate policy statements per resource type: IAM, EC2, S3, Lambda, RDS, ECS, DynamoDB, SNS, SQS
    - Scope each statement to specific resource ARNs (no wildcards except tagging)
    - Include only quarantine-relevant actions (no delete operations)
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [ ]* 8.2 Write property test for dynamic policy scoping
    - **Property 13: Dynamic Policy Scoping**
    - **Validates: Requirements 7.1, 7.2, 7.4**

- [ ] 9. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 10. Implement notifications and paging engine
  - [ ] 10.1 Implement AlertDispatcher with multi-channel support
    - Create `agent/paging.py` with `AlertDispatcher` class
    - Implement handler initialization from config (SNS, Email, Slack, Teams, PagerDuty)
    - Implement `send_investigation_complete_alert()` and `send_remediation_complete_alert()`
    - Implement `_dispatch_to_all()` — send to all enabled channels, failure in one doesn't block others
    - Implement `_send_with_retry()` — exponential backoff (2s, 4s, 8s) with max 3 attempts
    - Format messages per channel: JSON for SNS, markdown for Slack/Teams, structured payload for PagerDuty
    - _Requirements: 20.1, 20.2, 20.3, 20.4_

  - [ ] 10.2 Implement PagingEngine with intelligent rules
    - Implement `PagingEngine` class with `should_page()` method
    - Production accounts: always page (configurable severity threshold, default: MEDIUM)
    - Non-production accounts: page on HIGH/CRITICAL risk, resource creation, or cost > threshold ($100)
    - Unknown accounts: apply production rules (safe default)
    - Implement `_is_business_hours()` with configurable start/end hours, timezone, weekdays_only
    - _Requirements: 21.1, 21.2, 21.3, 21.4_

  - [ ] 10.3 Implement channel-specific alert handlers
    - Implement `SNSAlertHandler`, `EmailAlertHandler`, `SlackAlertHandler`, `TeamsAlertHandler`, `PagerDutyAlertHandler`
    - Each handler formats messages appropriately for its channel
    - _Requirements: 20.1, 20.4_

  - [ ]* 10.4 Write property test for alert dispatch to all channels
    - **Property 20: Alert Dispatch to All Channels**
    - **Validates: Requirements 20.2**

  - [ ]* 10.5 Write property test for notification retry with backoff
    - **Property 21: Notification Retry with Backoff**
    - **Validates: Requirements 20.3**

  - [ ]* 10.6 Write property test for paging decision logic
    - **Property 22: Paging Decision Logic**
    - **Validates: Requirements 21.1, 21.2, 21.3**

  - [ ]* 10.7 Write property test for business hours calculation
    - **Property 23: Business Hours Calculation**
    - **Validates: Requirements 21.4**

- [ ] 11. Implement investigation report generation
  - [ ] 11.1 Implement ReportGenerator with comprehensive markdown output
    - Create `agent/reporter.py` with `ReportGenerator` class
    - Implement `generate()` composing all report sections
    - Sections: header, executive summary, activity timeline, API activity (top 10 services, top 10 actions), resource activity (created/modified/deleted), IP address analysis, behavioral analysis, cost impact, recommended actions (10 standard recommendations)
    - _Requirements: 22.1, 22.2, 22.3, 22.4, 22.5_

  - [ ]* 11.2 Write property test for report generation completeness
    - **Property 24: Report Generation Completeness**
    - **Validates: Requirements 22.1, 22.2, 22.3, 22.4, 22.5**

- [ ] 12. Implement core agent orchestrator and remediation safety controls
  - [ ] 12.1 Implement CompromiseResponseAgent orchestrator
    - Create `agent/core.py` with `CompromiseResponseAgent` class
    - Implement `run()` — full detection sweep workflow
    - Implement `run_for_key()` — targeted key processing from EventBridge event
    - Implement `_process_alert()` — classify → analyze → remediate → report → notify
    - Implement `_classify_account()` — use OrganizationsConfigManager, default to PRODUCTION on failure
    - _Requirements: 9.1, 9.3, 10.5_

  - [ ] 12.2 Implement remediation authorization logic
    - Implement `_should_remediate()`:
      - PRODUCTION accounts: notify only, skip auto-remediation
      - NON_PRODUCTION with auto_remediation_enabled: proceed
      - force_remediation: bypass account type check
      - UNKNOWN accounts: treat as PRODUCTION
    - Implement `_execute_remediation()`:
      - Generate scoped IAM policy via DynamicPolicyGenerator
      - Attach policy to Remediation Agent role
      - Invoke Remediation Agent via bedrock-agentcore invoke_agent_runtime
      - Detach policy after remediation (always, via try/finally)
      - Fallback to local KeyRemediator on failure
    - _Requirements: 7.5, 9.2, 9.3, 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 12.3 Write property test for remediation authorization decision
    - **Property 16: Remediation Authorization Decision**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.5**

  - [ ]* 12.4 Write property test for remediation agent safety check
    - **Property 15: Remediation Agent Safety Check**
    - **Validates: Requirements 9.4, 9.5**

- [ ] 13. Implement event pipeline (Lambda trigger and EventBridge integration)
  - [ ] 13.1 Implement Lambda handler with event parsing
    - Create `lambda_handler.py` with `lambda_handler()` function
    - Implement `_parse_event()` dispatching to source-specific parsers
    - Implement `_parse_health_event()` — extract key from affectedEntities or eventDescription
    - Implement `_parse_security_hub_event()` — extract key from Resources[].Id (handle ARN, AWS::IAM, direct formats)
    - Implement `_parse_trusted_advisor_event()` — extract from check-item-detail
    - Implement `_parse_manual_event()` — pass through direct payload
    - Normalize all events into standard alert context: access_key_id, account_id, alert_source, event_type, region
    - _Requirements: 2.1, 2.2, 2.5_

  - [ ] 13.2 Implement AgentCore invocation from Lambda
    - Implement `_invoke_agentcore()` — invoke Investigation Agent via bedrock-agentcore invoke_agent_runtime API
    - Implement `_build_prompt()` — construct investigation prompt from alert context
    - Handle invocation failures gracefully
    - _Requirements: 2.3, 2.4_

  - [ ] 13.3 Implement AgentCore entrypoint for Investigation Agent
    - Create `agentcore_entrypoint.py` with BedrockAgentCoreApp
    - Implement `invoke()` entrypoint — load config, initialize agent, dispatch to `run()` or `run_for_key()`
    - _Requirements: 2.3_

  - [ ]* 13.4 Write property tests for event parsing
    - **Property 1: Event Parsing Extracts Correct Fields**
    - **Validates: Requirements 1.1, 1.2, 1.3**

  - [ ]* 13.5 Write property test for event normalization completeness
    - **Property 3: Event Normalization Produces Complete Context**
    - **Validates: Requirements 2.2**

  - [ ]* 13.6 Write property test for CloudTrail event parsing
    - **Property 5: CloudTrail Event Parsing Preserves Data**
    - **Validates: Requirements 3.4**

  - [ ]* 13.7 Write property test for event categorization
    - **Property 6: Event Categorization Is Correct**
    - **Validates: Requirements 3.5**

- [ ] 14. Implement CloudFormation infrastructure template
  - [ ] 14.1 Create CloudFormation template for event-driven pipeline
    - Create `infrastructure/cloudformation.yaml`
    - Define parameters: NotificationEmail, InvestigationAgentRuntimeId, RemediationAgentRuntimeId, InvestigationAgentRoleName, RemediationAgentRoleName
    - Deploy: Lambda function (Python 3.12, ARM64), 3 EventBridge rules, SNS topic (KMS encrypted), SQS dead-letter queue, IAM roles with least-privilege
    - Configure Lambda permissions for EventBridge invocation
    - Configure Investigation Agent role: SNS publish, invoke Remediation Agent, manage Remediation Agent IAM policies
    - _Requirements: 24.1, 24.2, 24.3, 24.4_

- [ ] 15. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 16. Implement resource tagging and deploy EventBridge script
  - [ ] 16.1 Implement resource tagging for quarantined resources
    - Ensure all quarantine handlers tag resources with: QuarantinedBy="AccessKeyCompromiseAgent", QuarantineReason="CompromisedAccessKey", QuarantineTimestamp=UTC ISO 8601
    - _Requirements: 19.1, 19.2, 19.3_

  - [ ] 16.2 Create EventBridge deployment script
    - Create `deploy_eventbridge.py` for updating Lambda code in the CloudFormation stack
    - _Requirements: 24.1_

  - [ ]* 16.3 Write property test for resource tagging completeness
    - **Property 19: Resource Tagging Completeness**
    - **Validates: Requirements 19.1, 19.2, 19.3**

- [ ] 17. Implement end-to-end tests and integration validation
  - [ ]* 17.1 Write E2E simulation test
    - Create `test_e2e_simulation.py` — simulate full workflow with mocked AWS services
    - Test: event receipt → detection → analysis → remediation → report → notification
    - Validate all components integrate correctly
    - _Requirements: 1.1–1.5, 2.1–2.5, 3.1–3.5, 9.1–9.5, 10.1–10.5_

  - [ ]* 17.2 Write E2E full remediation test
    - Create `test_e2e_full_remediation.py` — test remediation workflow against real or mocked AWS
    - Test: key deactivation, IAM quarantine, EC2 quarantine, S3 quarantine, Lambda quarantine
    - Validate quarantine-only philosophy (no deletions)
    - _Requirements: 11.1–11.4, 13.1–13.5, 15.1–15.4, 16.1–16.2, 25.1–25.4_

  - [ ]* 17.3 Write property test for dry-run mode
    - **Property 17: Dry-Run Mode Prevents Execution**
    - **Validates: Requirements 10.4**

- [ ] 18. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The implementation uses Python 3.12 as specified in the design and tech stack
- All quarantine handlers follow the quarantine-only philosophy — never delete resources
- The two-agent architecture (Investigation read-only, Remediation dynamic write) minimizes blast radius

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["1.4", "2.1", "2.2", "2.3", "2.4", "2.5"] },
    { "id": 3, "tasks": ["2.6", "3.1", "3.2"] },
    { "id": 4, "tasks": ["3.3", "3.4", "5.1"] },
    { "id": 5, "tasks": ["5.2", "5.3", "5.4", "6.1"] },
    { "id": 6, "tasks": ["5.5", "5.6", "5.7", "5.8", "5.9", "6.2"] },
    { "id": 7, "tasks": ["6.3", "6.4", "7.1"] },
    { "id": 8, "tasks": ["7.2", "7.3", "8.1"] },
    { "id": 9, "tasks": ["7.4", "7.5", "8.2", "10.1", "10.2", "10.3", "11.1"] },
    { "id": 10, "tasks": ["10.4", "10.5", "10.6", "10.7", "11.2", "12.1"] },
    { "id": 11, "tasks": ["12.2", "13.1"] },
    { "id": 12, "tasks": ["12.3", "12.4", "13.2", "13.3"] },
    { "id": 13, "tasks": ["13.4", "13.5", "13.6", "13.7", "14.1"] },
    { "id": 14, "tasks": ["16.1", "16.2"] },
    { "id": 15, "tasks": ["16.3", "17.1", "17.2", "17.3"] }
  ]
}
```
