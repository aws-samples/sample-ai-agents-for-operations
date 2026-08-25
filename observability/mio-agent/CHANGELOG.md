# Changelog

All notable changes to aws-mio-agent are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.1.0] - 2025-01-15

### Added
- Initial release of MIO Agent (Monitoring Intelligence and Observability Agent)
- Pydantic v2 data models: AssessmentRequest, OMS, Finding, TAMBrief, CustomerReport, LeadershipSummary
- CloudWatch Analyst Agent: alarms, metrics coverage, log groups, X-Ray tracing, dashboards
- IaC Scanner Agent: CloudFormation, CDK, Terraform, SAM template analysis
- Third-Party Validator Agent: Datadog, Dynatrace, New Relic, Splunk coverage detection
- Narrative Agent: Amazon Bedrock Claude 3.5 Sonnet for audience-appropriate report generation
- Coordinator orchestrator with three-tier access model (Tier 1/2/3)
- OMS scoring engine with weighted 5-dimension scoring and trend calculation
- Five event-driven triggers: support case, deployment, health event, scheduled, on-demand API
- AWS CDK infrastructure stack: DynamoDB, S3, SQS, Lambda, API Gateway, EventBridge
- Unit tests with moto AWS mocking
- Full documentation: architecture, deployment guide, TAM user guide, customer onboarding
- Sample outputs: TAM brief, customer report, leadership summary
