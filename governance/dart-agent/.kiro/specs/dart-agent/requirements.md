# DART Agent — Requirements

**Agent:** DART (Dataset Audit & Readiness for Training)  
**Tagline:** Know before you train.  
**Pattern:** Strands on Fargate  

---

## Problem

ML teams fine-tuning LLMs on Amazon SageMaker or Amazon Bedrock regularly burn expensive training runs on datasets that were never properly validated. A single failed 3-day training job on a p4d.24xlarge cluster costs $15,000–$50,000 in compute alone. The root causes are almost always data-related: duplicate records that cause model memorisation, PII that creates compliance exposure, empty responses that cause loss spikes, token budgets that exceed the model's context window, or train/validation data leakage that inflates eval scores.

No single AWS service catches all of these issues in a unified pre-training workflow. SageMaker Debugger monitors during training (too late). SageMaker Clarify audits bias post-training (too late). Data Wrangler does ETL but is not LLM-training-aware. Amazon Comprehend detects PII but is not wired to the fine-tuning workflow.

The gap is a pre-flight check: an agent that inspects training data *before* the job is submitted, reasons across multiple quality dimensions, and gives a clear GO / NO-GO / GO WITH FIXES recommendation with evidence and cost impact.

---

## Solution

DART Agent is an AI agent that runs before an LLM fine-tuning job is submitted. It accepts a dataset path (S3 or local), a target model identifier, and optional configuration. It then runs 7 tools in sequence, reasons across the results, and produces a Pre-Flight Report with a final recommendation.

The agent uses the Strands Agents SDK with Amazon Bedrock (Claude Sonnet 4) for orchestration and reasoning. It is deployed on ECS Fargate to handle large multi-GB datasets without Lambda timeout constraints.

---

## Requirements

### Functional Requirements

#### Input Handling
- MUST accept datasets from S3 paths (`s3://bucket/key`) and local file paths
- MUST support formats: JSONL, JSON, CSV, Parquet, TXT
- MUST auto-detect format from file extension and content sniffing
- MUST accept a target model identifier (e.g., `meta.llama3-70b-instruct-v1:0`, `anthropic.claude-haiku-4-5`)
- MUST accept optional configuration: similarity threshold for dedup, PII redaction strategy (mask/remove), max token budget override

#### Dataset Profiling (`profile_dataset`)
- MUST detect schema: field names, types, null rates, cardinality
- MUST compute text length statistics (min, max, mean, p50, p95) for text fields
- MUST detect and report empty or near-empty responses (< 10 tokens)
- MUST estimate total token count using tiktoken for the target model's tokenizer family
- MUST detect mixed encodings (UTF-8, Latin-1, Windows-1252) and flag anomalies
- MUST output a structured profile dict

#### Duplicate Detection (`detect_duplicates`)
- MUST detect exact duplicates (hash-based)
- MUST detect near-duplicates using MinHash with Jaccard similarity
- MUST use a configurable similarity threshold (default: 0.85)
- MUST report: exact duplicate count, near-duplicate count, affected record indices
- MUST estimate GPU compute cost wasted by duplicates (based on token count × duplicate ratio)

#### PII Scanning (`scan_pii`)
- MUST use Amazon Comprehend DetectPiiEntities for PII detection
- MUST detect: EMAIL, PHONE, NAME, ADDRESS, SSN, CREDIT_DEBIT_NUMBER, DATE_TIME (configurable)
- MUST support redaction strategies: MASK (replace with [REDACTED_TYPE]), REMOVE (delete record)
- MUST report: PII entity count by type, affected record count, sample affected records (truncated)
- MUST produce a redacted copy of the dataset when redaction is applied
- MUST log all PII detections to DynamoDB for EU AI Act compliance audit trail

#### Training Viability Check (`check_training_viability`)
- MUST validate token budget: total tokens ≤ model's max context × record count threshold
- MUST detect train/validation data leakage (exact record overlap between splits)
- MUST check minimum dataset size for target model (warn if < recommended minimum)
- MUST validate schema completeness for known fine-tuning formats (instruction/response, prompt/completion, messages/chat)
- MUST flag records where response length < 10 tokens (low-quality signal)

#### Cost Estimation (`estimate_training_cost`)
- MUST estimate training cost based on: dataset token count, target model, instance type, estimated epochs
- MUST use SageMaker instance pricing for self-managed training
- MUST use Bedrock fine-tuning pricing for managed fine-tuning
- MUST show cost before and after applying fixes (dedup, empty row removal)
- MUST express savings in dollars

#### Safe Auto-Fix (`apply_safe_fixes`)
- MUST remove exact duplicates automatically (no approval required)
- MUST remove records with empty/null required fields automatically
- MUST normalise text encoding to UTF-8 automatically
- MUST NOT modify record content (PII redaction, field edits require explicit approval)
- MUST produce a new dataset file with fixes applied, preserving the original
- MUST report: records removed, records modified, final record count

#### Pre-Flight Report (`generate_preflight_report`)
- MUST produce a structured report with: overall recommendation (GO / NO-GO / GO WITH FIXES), quality score (0–100), findings by severity (Critical/High/Medium/Low), cost before/after fixes
- MUST include specific evidence for each finding (record count, examples, estimated impact)
- MUST include an ordered action list ranked by severity
- MUST be human-readable as both plain text and structured JSON
- MUST be stored to S3 and DynamoDB for audit trail

#### Agent Orchestration
- MUST support natural language interaction: "Check my dataset at s3://bucket/data.jsonl for fine-tuning llama3-70b"
- MUST support step-by-step mode: user can approve/reject each fix before applying
- MUST support autonomous mode: apply all safe fixes automatically
- MUST ask for approval before any destructive operation (PII redaction, record removal beyond safe-fixes)

### Non-Functional Requirements
- MUST process a 50K record JSONL dataset in under 5 minutes on a 4 vCPU / 8GB Fargate task
- MUST handle datasets up to 10GB by streaming (not loading entirely into memory)
- MUST be deployable with a single `cdk deploy` command
- MUST emit structured JSON logs to CloudWatch
- MUST tag all AWS resources with: `project=dart-agent`, `environment`, `owner`
- MUST store no training data in Bedrock or any external service (data stays in customer's S3)

### Security Requirements
- Customer dataset access is READ-ONLY by default (IAM boundary enforced)
- Redacted output written to a separate S3 prefix, never overwrites input
- All secrets (if any) stored in AWS Secrets Manager
- No training data sent to Bedrock — only metadata and statistical summaries
- PII detection results stored in DynamoDB with TTL (default 90 days)
- Comprehend calls use batch API to minimise data transfer

### Out of Scope (v1)
- Bias/representativeness auditing (use SageMaker Clarify)
- Synthetic data generation
- Post-training feedback loop (use SageMaker Model Monitor)
- Dataset versioning beyond run-level snapshots
- Web UI / Streamlit dashboard
