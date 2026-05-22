# Security Guidelines by AWS Service

## Lambda
- Reserved concurrency limits prevent resource exhaustion (10 per function)
- Environment variables encrypted with AWS KMS customer-managed key
- Dead Letter Queue configured for all functions
- Timeout set appropriately (900s for AgentCore calls, 300s for Bedrock, 30s for Slack)

## DynamoDB (Approval Tokens)
- Encryption at rest with customer-managed AWS KMS key (annual rotation)
- Point-in-time recovery enabled
- TTL enabled for automatic token expiration (7 days)
- Conditional writes prevent token collision and reuse

## Amazon Bedrock
- Model access scoped to specific foundation model ARNs
- Structured prompts with explicit delimiters prevent injection
- Output validated against JSON schema before processing
- Human approval required before any remediation execution

## SNS
- Topic encrypted with customer-managed AWS KMS key
- Publish permissions scoped to specific Lambda execution roles
- Topic ARN referenced explicitly in IAM policies

## SES
- Sender identity verified (sandbox mode)
- IAM permissions scoped to specific verified identity ARN
- STARTTLS enforced for outbound delivery

## S3 (Routing Config)
- Block Public Access enabled (all four settings)
- Server-side encryption (AES-256)
- Versioning enabled for audit trail
- Bucket policy enforces TLS-only access
- Server access logging to dedicated log bucket
- File extension validation before processing

## API Gateway
- Access logging enabled (CloudWatch)
- HTTPS-only (no HTTP endpoints)
- Default throttling (10,000 req/s burst)

## AWS KMS
- Key rotation enabled (annual, automatic)
- Service principal access restricted with Condition constraints
- Separate key from Secrets Manager (uses AWS-managed key)

## Secrets Manager
- Jira API token stored securely (never in code)
- Access scoped to specific secret ARN in IAM policies
- CloudTrail logs all GetSecretValue/PutSecretValue calls

## EventBridge
- Event pattern filtering restricts to `aws.health` and `aha` sources
- Events processed only with status "open" or "upcoming"
- Custom bus (`aha-eb01`) isolates AHA events from default bus

## AWS Organizations
- Read-only access (Describe/List only)
- Wildcard Resource required (service limitation) — documented with compensating controls
- CloudTrail logs all Organizations API calls

## Amazon EKS
- Cluster names extracted from event payload only (never user input)
- Describe actions scoped to cluster ARNs in account/region
- Upgrade operations gated behind human approval token
- List actions require wildcard (service limitation) — documented
