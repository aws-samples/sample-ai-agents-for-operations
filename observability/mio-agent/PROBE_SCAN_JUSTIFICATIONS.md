# Probe Scan Finding Justifications

This document justifies findings from the Probe security scan that are false positives or intentionally accepted.

## Resolved in v0.1.6

### ERROR: urllib3 CVEs (GHSA-gm62-xv2j-4w53, GHSA-38jv-5279-wg99, GHSA-2xpw-w6gg-jr37, GHSA-2xpw-w6gg-jr37)
- **Finding:** grype flagged 3 CRITICALs and 2 WARNINGs in `urllib3==2.4.0`
  - GHSA-gm62-xv2j-4w53: unbounded decompression chain links
  - GHSA-38jv-5279-wg99: decompression-bomb safeguards bypassed via redirect
  - GHSA-2xpw-w6gg-jr37: streaming API improperly handles highly compressed data
  - GHSA-48p4-8xcf-vxj5: redirects not controlled in browsers/Node.js (WARNING)
  - GHSA-pq67-6m6q-mj2v: redirects not disabled when retries disabled (WARNING)
- **Fix:** Upgraded `urllib3` from `2.4.0` to `2.7.0` in `requirements.txt` (CVEs were not patched until 2.6.0; 2.7.0 is the current stable release)
- **Status:** RESOLVED

### WARNING: semgrep `is-function-without-parentheses` in `pipeline.py` and `finding_validator.py`
- **Finding:** semgrep rule `is-function-without-parentheses` flagged `gate_result.is_blocked` and `result.is_valid` as potentially missing parentheses
- **Justification:** These are not methods — `is_blocked` is a `@property` on the `GateResult` dataclass, and `is_valid` is a `bool` field on the `ValidationResult` dataclass. Neither requires `()`. Semgrep cannot distinguish properties/fields from methods without type information.
- **Fix:** Added `# nosemgrep: is-function-without-parentheses` suppression comments alongside existing `# noqa` directives on all 4 flagged lines.
- **Status:** RESOLVED (suppressed with justification)

### WARNING: checkov — Test fixture `tests/fixtures/sample_iac_template.json`
- **Findings:** CKV_AWS_117, CKV_AWS_115, CKV_AWS_116, CKV_AWS_157, CKV_AWS_161, CKV_AWS_76, CKV_AWS_73, CKV_AWS_120
- **Justification:** `tests/fixtures/sample_iac_template.json` is a **unit test fixture** used to validate MIO Agent's IaC gap-detection logic. It intentionally contains incomplete infrastructure configuration so the scanner has gaps to detect. It is never deployed. The production CDK stack in `infrastructure/stacks/` has all security controls properly configured.
- **Fix:** Created `.checkov.yml` at project root with `skip-path: tests/fixtures` and explicit `skip-check` entries for all 8 CKV IDs.
- **Status:** RESOLVED (suppressed via `.checkov.yml`)

## Resolved in v0.1.5

### CRITICAL: urllib3 CVEs (GHSA-gm62-xv2j-4w53, GHSA-38jv-5279-wg99, GHSA-2xpw-w6gg-jr37)
- **Fix:** Upgraded `urllib3` from `2.2.3` to `2.4.0` in `requirements.txt`
- **Status:** RESOLVED

## False Positives

### bandit B105 — Hardcoded password in `confidence_gate.py` lines 34-35
- **Finding:** `B105: Possible hardcoded password: 'PASS'` and `'PASS_WITH_WARNINGS'`
- **Justification:** These are enum string values for a `GateDecision` class, not passwords or credentials. The strings `"PASS"` and `"PASS_WITH_WARNINGS"` represent gate decision states.
- **Resolution:** Added `# nosec B105` comments to suppress.

### bandit B311 — Pseudo-random in `bedrock_client.py` line 100
- **Finding:** `B311: Standard pseudo-random generators are not suitable for security/cryptographic purposes`
- **Justification:** `random.random()` is used solely for jitter in exponential backoff retry logic (adding 0.5–1.5 seconds of random delay). This is not used for security, authentication, or cryptographic purposes. Jitter intentionally does not need to be cryptographically secure.
- **Resolution:** Added `# nosec B311` comment to suppress.

### checkov — Test fixture `tests/fixtures/sample_iac_template.json`
- **Findings:** CKV_AWS_117 (VPC), CKV_AWS_115 (concurrency), CKV_AWS_116 (DLQ), CKV_AWS_157 (Multi-AZ), CKV_AWS_16 (RDS encryption), CKV_AWS_161 (IAM auth), CKV_AWS_76 (access logging), CKV_AWS_73 (X-Ray), CKV_AWS_120 (caching)
- **Justification:** This is a **test fixture** used by unit tests to validate MIO Agent's IaC scanning logic. It intentionally contains incomplete configuration to test gap detection. The production CDK stack in `infrastructure/stacks/mio_agent_stack.py` has all security controls properly configured.
- **Resolution:** Added checkov skip metadata to the fixture file with explanatory comment.

### semgrep — `is_valid`, `is_blocked` attribute access
- **Findings:** `is-function-without-parentheses` in `finding_validator.py` and `pipeline.py`
- **Justification:** `is_valid` is a field of the `ValidationResult` dataclass, and `is_blocked` is a `@property` of `GateResult`. Neither is a method requiring `()`. Semgrep incorrectly flags these as potential missing parentheses.
- **Resolution:** Added `# noqa` suppression comments.
