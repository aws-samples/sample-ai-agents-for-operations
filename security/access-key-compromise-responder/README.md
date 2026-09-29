# AKC Responder — Access Key Compromise Response

An event-driven, **advisory** (read-only) solution that uses the managed **AWS
DevOps Agent** to investigate compromised or exposed AWS access keys and produce
**human-run** containment recommendations. Nothing is quarantined, stopped, or
deleted automatically — the agent investigates and recommends; a human engineer
reviews and acts.

## Why this design

Compromised access-key response is normally slow and manual. Fully automating
the *remediation* makes many teams uncomfortable, because they don't want an
agent taking mutating actions on their behalf. This solution keeps the fast,
automated **investigation** but hands the **decision and the action to a human**,
delivered as clear, copy-pasteable steps.

It is built on the managed **AWS DevOps Agent** product (not a self-hosted
agent). The investigation intelligence lives in a portable **Skill**
(`SKILL.md` + references). A small serverless **pipeline** wires the existing
detection sources to the agent and routes its findings to people.

## Architecture

```
AWS Health  ─┐
Security Hub ─┤  3 EventBridge rules
(GuardDuty)  ─┤        │
Trusted      ─┘        ▼
Advisor           Event Router Lambda   (parse event, extract key,
                       │                 enrich account context — read-only)
                       ▼
                Step Functions workflow
                       │  invoke DevOps Agent (HMAC-signed webhook)
                       ▼
                AWS DevOps Agent  ──uses──►  access-key-compromise skill
                (managed)                    (investigation methodology)
                       │
                       ▼
                Heads-up notification ──► Slack / Teams (if configured)
                                     └──► SNS email (default fallback) +
                                          account alternate contacts
                       │
                       ▼
        Findings + human-run recommendations reviewed
        in the DevOps Agent operator app
```

The agent **only reads** (CloudTrail, IAM, account metadata, resource describe
calls) and produces an investigation report plus per-resource recommendations.
Humans run the steps in
`skill/access-key-compromise-investigation/references/remediation-playbook.md`.

## Repository layout

```
.
├── template.yaml                      # SAM template — the canonical, one-step deploy
├── deploy.sh                          # convenience wrapper around `sam build` + `sam deploy`
├── skill/access-key-compromise-investigation/
│   ├── SKILL.md                       # investigation methodology + output contract
│   └── references/
│       ├── remediation-playbook.md    # per-resource manual CLI/Console steps (evidence-first)
│       ├── api-classification.md      # Creates/Modifies/Grants/Exfil/Read-only + persistence hunt
│       ├── risk-scoring.md            # anomaly signals + severity rubric
│       └── detection-sources.md       # raw event formats + key extraction
├── skill/access-key-compromise-investigation.zip   # packaged skill for console upload
├── pipeline/lambda/
│   ├── event_router/index.py          # parse 3 sources + enrich account context + start workflow
│   ├── investigation_callback/index.py# invoke DevOps Agent webhook (HMAC-signed)
│   └── notifier/index.py              # heads-up notification (SNS/Slack/Teams)
├── events/                            # sample test events (invoke the Event Router directly)
└── KB-ARTICLE.md                      # shareable write-up of the solution
```

## Prerequisites

- An **AWS DevOps Agent Space** with topology discovery enabled.
- An active **CloudTrail** trail capturing management events.
- **AWS SAM CLI** and **AWS CLI v2**, authenticated to the target account/Region
  with permission to deploy IAM, Lambda, Step Functions, EventBridge, SNS, and
  Secrets Manager.
- Deploy in the Region where you want the pipeline (the agent queries across
  Regions).

## Deploy (about 15 minutes)

### 1. Upload the skill to your DevOps Agent Space

Either:
- **Import from repository** (recommended): DevOps Agent console → Knowledge →
  Skills → Add skill → Import from repository → point at
  `skill/access-key-compromise-investigation`; or
- **Upload the zip**: upload `skill/access-key-compromise-investigation.zip`
  (≤ 6 MB).

Confirm the skill shows as **Active**.

### 2. Get the Agent Space webhook URL + secret

In the DevOps Agent console, create/read the **webhook** for your Agent Space.
Note the URL and choose an HMAC signing secret (you'll pass both at deploy time).

### 3. Deploy the pipeline (one step)

The SAM template packages the real Lambda code automatically — there is no
manual zipping or S3 upload.

**Guided (interactive):**

```bash
./deploy.sh
# or, equivalently:
sam build && sam deploy --guided --stack-name AKCResponder --capabilities CAPABILITY_IAM
```

**Non-interactive (CI / scripted):**

```bash
NOTIFICATION_EMAIL=you@example.com \
DEVOPS_AGENT_WEBHOOK_URL='https://<agent-space-webhook-url>' \
WEBHOOK_SECRET='<hmac-secret>' \
./deploy.sh --non-interactive
```

Optional parameters: `SLACK_WEBHOOK_URL`, `MS_TEAMS_WEBHOOK_URL`.

### 4. Confirm the SNS email subscription

Click the confirmation link sent to the notification email you provided.

## Test

`aws.health`, `aws.securityhub`, and `aws.trustedadvisor` are reserved
EventBridge sources, so you can't publish synthetic events onto the bus.
Instead, invoke the Event Router directly with a sample:

```bash
aws lambda invoke \
  --function-name AKCResponder-EventRouter \
  --payload fileb://events/test-health-exposed-key.json \
  --cli-binary-format raw-in-base64-out \
  /tmp/out.json && cat /tmp/out.json
```

Then watch the Step Functions execution, the investigation in the DevOps Agent
operator app, and the notification (email/Slack/Teams).

Other samples: `test-securityhub-guardduty.json`,
`test-trustedadvisor-exposed-keys.json`,
`test-manual-temporary-credential.json` (exercises the `ASIA` session-revocation
path).

## What the agent produces

- A structured investigation report (the `SKILL.md` output contract): summary,
  findings, affected-resources table, and recommendations across the full
  incident lifecycle (**Detect → Analyze → Contain → Eradicate → Recover →
  Post-incident**).
- One recommendation per questioned resource/action, evidence-first, with both
  **AWS CLI and Console** steps for a human to run.
- Delivered to the DevOps Agent Space (Artifacts/Recommendations) and your
  notification channels.

## Safety posture

- **Read-only / advisory.** No mutating API is called by the pipeline or agent.
- **Evidence-first.** Recommendations preserve forensics (snapshots, versions)
  before any stop/isolate/delete.
- **Uses `AWSCompromisedKeyQuarantineV3`** as the recommended IAM quarantine
  action, with a warning not to remove it until remediation is complete.
- **Handles both `AKIA` (long-term) and `ASIA` (temporary/STS)** credentials,
  including role session revocation.

See [KB-ARTICLE.md](KB-ARTICLE.md) for a full write-up of the solution.

## Known limitations & assumptions

- **CloudTrail must be enabled.** The investigation reads up to 90 days of
  management-event history. Without an active trail (or with a shorter
  retention window), the agent's activity analysis is incomplete.
- **The skill must be uploaded to your DevOps Agent Space** and show as
  **Active** before the pipeline can produce findings. Deploying the pipeline
  alone is not enough.
- **AWS Organizations access is required for account-name and alternate-contact
  resolution.** In a standalone account, or without the relevant Organizations
  permissions, the notifications fall back to the raw account ID and omit
  alternate contacts.
- **Advisory only.** The pipeline and agent never mutate customer resources.
  All containment steps are recommendations a human must review and run.
- **Reserved EventBridge sources.** `aws.health`, `aws.securityhub`, and
  `aws.trustedadvisor` are reserved, so you cannot publish synthetic events for
  testing — invoke the Event Router Lambda directly with the sample events
  instead (see [Test](#test)).
- **Region scope.** The pipeline runs in a single Region, though the agent
  queries across Regions during an investigation. Deploy in the Region where
  you want the pipeline to live.

## Troubleshooting

- **No investigation starts after a detection.** Confirm the skill is **Active**
  in the DevOps Agent Space and that `DEVOPS_AGENT_WEBHOOK_URL` is set. Check
  the Step Functions execution history and the Event Router Lambda logs in
  Amazon CloudWatch Logs.
- **Webhook invocation fails (HTTP 4xx/5xx or "unreachable").** The Investigation
  Callback Lambda now logs the HTTP status and response body. A 401/403 usually
  means the HMAC signing secret (`WEBHOOK_SECRET`) does not match the Agent
  Space webhook; a connection/timeout error means the URL is wrong or the
  endpoint is unreachable.
- **No notification received.** First confirm the Amazon SNS email subscription
  (click the confirmation link). If Slack/Teams was configured but silent, the
  Notifier logs a `... delivery failed, falling back to SNS` message with the
  endpoint host and status — a 404 typically means the webhook was revoked or
  the URL is wrong.
- **Missing account name / alternate contacts in the message.** The account
  context could not be resolved — verify AWS Organizations access and that
  alternate contacts are configured on the target account.
- **`AccessDenied` during an investigation.** The DevOps Agent's role lacks a
  required read permission (CloudTrail, IAM, or resource describe calls).
  Grant the missing read-only permission and re-run.

## Security

See [CONTRIBUTING.md](CONTRIBUTING.md#security-issue-notifications) for
information about reporting security issues. If you discover a potential
security issue, please notify AWS/Amazon Security via our
[vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/)
rather than opening a public GitHub issue.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE)
file.
