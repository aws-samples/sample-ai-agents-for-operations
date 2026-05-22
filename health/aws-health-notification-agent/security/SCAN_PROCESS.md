<!-- Copyright (c) 2026 Amazon Web Services -->
<!-- Licensed under the MIT-0 License -->
<!-- See LICENSE file in the project root for full license information. -->

# Security Scan Process

## Overview

This project undergoes automated security scanning as part of the CSR (Content Security Review) process using the following tools:

| Tool | Type | Scope |
|---|---|---|
| Bandit | Python SAST | All `.py` files |
| Semgrep | Multi-language SAST | Python, Dockerfile, Shell |
| Checkov | IaC scanning | CloudFormation/SAM templates |
| cfn_nag | CloudFormation linting | SAM templates |
| Slingshot | Rubric-based compliance | Full repository |

## Scan Cadence

- **Pre-push**: Automated via Kiro hook (manual trigger) or git pre-push hook
- **Pre-merge**: Probe scan runs on every MR to `main`
- **CSR submission**: Full Slingshot scan with rubric evaluation

## Process

1. **Run automated scans** — Probe (Bandit + Semgrep + Checkov + cfn_nag) and Slingshot
2. **Document findings** — Results stored in `security/scan-results/`
3. **Triage findings** — Classify as Fix, Suppress (with justification), or Accept (with risk documentation)
4. **Remediate** — Fix code issues, add suppression comments for false positives
5. **Verify** — Re-run scans to confirm findings are resolved
6. **Attest** — Document verification in this file

## Suppression Mechanisms

| Tool | Suppression Format |
|---|---|
| Bandit | `# nosec B310` (inline comment) |
| Semgrep | `# nosemgrep: rule-id` (inline comment) |
| Checkov | `#checkov:skip=CKV_ID:reason` (new line inside resource scope) |
| cfn_nag | `Metadata.cfn_nag.rules_to_suppress` (YAML block) |

## Accepted Risks

See `security/accepted-risks.csv` for the full list of suppressed findings with justifications.

## Latest Scan Results

### Scan Date: May 09, 2026

**Probe Scan (Bandit + Semgrep + Checkov + cfn_nag):**
- Critical: 0
- High: 0 (all suppressed with documented justification)
- Warning: 0 (all suppressed or fixed)

**Slingshot Scan (Rubric):**
- Critical: 0
- High: Addressed or documented as accepted risks

### Mitigation Verification

- **Verified by:** Automated re-scan after remediation
- **Verification date:** May 09, 2026
- **Attestation:** All Critical/High findings have been addressed with code fixes or documented compensating controls in `security/accepted-risks.csv`

## How to Run Scans Locally

```bash
# Bandit (Python SAST)
pip install bandit
bandit -r phd_notification_classifier/ aha_eventbridge_lambda/ routing_config_lambda/ routing_approval_lambda/ approval_lambda/ -f json -o security/scan-results/bandit-results.json

# Semgrep
pip install semgrep
semgrep --config auto . --json -o security/scan-results/semgrep-results.json

# Checkov (IaC)
pip install checkov
checkov -d . --framework cloudformation --output-file-path security/scan-results/ --soft-fail

# cfn_nag
gem install cfn-nag
cfn_nag_scan --input-path aha_eventbridge_lambda/template.yaml --output-format json > security/scan-results/cfn-nag-results.json
```
