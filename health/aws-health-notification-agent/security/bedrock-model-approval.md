<!-- Copyright (c) 2026 Amazon Web Services -->
<!-- Licensed under the MIT-0 License -->
<!-- See LICENSE file in the project root for full license information. -->

# Amazon Bedrock Model Approval

## Approved Model

- **Model:** Anthropic Claude Sonnet 4 (`eu.anthropic.claude-sonnet-4-20250514-v1:0`)
- **Provider:** Anthropic (via Amazon Bedrock marketplace)
- **Region:** eu-west-1 (regional prefix: `eu.`)

## Legal Approval

- **Status:** Pre-approved via Amazon Bedrock marketplace
- **Basis:** Models available through Amazon Bedrock marketplace are pre-approved under AWS Service Terms
- **No separate legal review required** for marketplace models
- **Review date:** May 2026

## Approved Use Cases

- AWS Health event classification (BREAKING_CHANGE, COST_IMPLICATION, SECURITY_RELATED)
- Routing document parsing (CSV/JSON/text → structured routing JSON)
- Impact analysis and remediation step generation

## Security Review

- **Data handling:** Amazon Bedrock guarantee — customer data not used for model training
- **Data residency:** Processed within the configured AWS region only
- **Access control:** IAM policy restricts `bedrock:InvokeModel` to specific foundation model ARNs
- **Encryption:** TLS 1.2+ in transit; no data persisted by the model service
- **Monitoring:** All invocations logged via CloudWatch and CloudTrail

## Model Version Change Management

1. Model version changes require updating `BEDROCK_MODEL_ID` environment variable
2. Test classification accuracy on historical events before deploying new version
3. Update this document with new model version and review date
4. No separate legal review needed for version updates within same model family on Bedrock marketplace

## References

- [Amazon Bedrock Service Terms](https://aws.amazon.com/service-terms/)
- [Amazon Bedrock Data Protection](https://docs.aws.amazon.com/bedrock/latest/userguide/data-protection.html)
- security/SECURITY.md "3rd Party Service Approvals" section
