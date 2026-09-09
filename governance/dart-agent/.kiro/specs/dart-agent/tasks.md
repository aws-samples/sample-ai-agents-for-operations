# DART Agent — Implementation Tasks

---

## Phase 1: Foundation

- [x] Create feature branch `feat/dart-agent`
- [x] Write requirements.md
- [x] Write design.md
- [ ] Scaffold project directory structure
- [ ] Write `src/dart-agent/config.py`
- [ ] Write `src/dart-agent/prompts/system_prompt.md`

## Phase 2: Tools

- [ ] `tools/profile_dataset.py` — format detection, schema, stats, token estimation
- [ ] `tools/detect_duplicates.py` — exact hash + MinHash near-duplicate
- [ ] `tools/scan_pii.py` — Amazon Comprehend PII detection + redaction
- [ ] `tools/check_training_viability.py` — token budget, leakage, format validation
- [ ] `tools/estimate_training_cost.py` — SageMaker + Bedrock pricing
- [ ] `tools/apply_safe_fixes.py` — safe auto-fix (dedup, empty rows, encoding)
- [ ] `tools/generate_preflight_report.py` — final report synthesis

## Phase 3: Agent Entrypoint

- [ ] `src/dart-agent/agent.py` — Strands agent with all 7 tools registered
- [ ] ECS Fargate handler + health check endpoint

## Phase 4: Tests

- [ ] `tests/unit/test_profile_dataset.py`
- [ ] `tests/unit/test_detect_duplicates.py`
- [ ] `tests/unit/test_scan_pii.py`
- [ ] `tests/unit/test_check_training_viability.py`
- [ ] `tests/unit/test_estimate_training_cost.py`
- [ ] `tests/unit/test_apply_safe_fixes.py`
- [ ] `tests/unit/test_generate_preflight_report.py`
- [ ] `tests/integration/test_agent_e2e.py`

## Phase 5: Infrastructure

- [ ] `infra/lib/DartAgentStack.ts` — CDK stack (Fargate, IAM, S3, DynamoDB, ECR, CW)
- [ ] `infra/bin/dart-agent.ts` — CDK app entrypoint
- [ ] `infra/cdk.json`

## Phase 6: Packaging

- [ ] `Dockerfile` — python:3.12-slim, non-root user
- [ ] `requirements.txt` — pinned dependencies
- [ ] `Makefile` — build, test, deploy, lint targets
- [ ] `.gitignore`

## Phase 7: Documentation

- [ ] `README.md` — public-facing, aws-samples ready
- [ ] `MUTATING_ACTIONS.md` — optional write permissions documentation

## Phase 8: Release

- [ ] Commit all files to `feat/dart-agent`
- [ ] Verify Python syntax (no import errors)
- [ ] Git log review
