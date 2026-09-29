---
name: access-key-compromise-investigation
description: >-
  Investigate compromised or exposed AWS IAM access keys and temporary (STS)
  credentials, then produce evidence-first, human-run containment
  recommendations. Use when investigating exposed or leaked access keys,
  keys committed to public repositories, or suspected unauthorized use of AWS
  credentials — including AWS Health AWS_RISK_CREDENTIALS_EXPOSED events,
  Amazon GuardDuty CredentialAccess/UnauthorizedAccess IAMUser findings in AWS
  Security Hub, Trusted Advisor "Exposed Access Keys" checks, or a reported
  AKIA/ASIA key leak, unfamiliar-IP/Region usage, unexpected resource
  creation, IAM privilege escalation, or billing spikes. This skill
  investigates and recommends only; it never mutates, stops, deletes, or
  quarantines any resource — a human reviews the findings and runs every action.
---

# Access Key Compromise Investigation and Containment Guidance

## When to use this skill (trigger scenarios)

Use this skill when a long-term IAM access key (`AKIA...`), a temporary STS
credential (`ASIA...`), or an IAM principal's credentials are suspected to be
exposed or compromised. Specific triggers include:

- Investigating exposed credentials, leaked access keys on public
  repositories, or suspected unauthorized use of AWS credentials.
- AWS Health `AWS_RISK_CREDENTIALS_EXPOSED` events.
- Amazon GuardDuty `CredentialAccess:IAMUser/CompromisedCredentials` and other
  `UnauthorizedAccess`/`CredentialAccess` IAM finding types surfaced through
  AWS Security Hub.
- AWS Trusted Advisor "Exposed Access Keys" checks.
- An engineer reports an `AKIA` or `ASIA` key was leaked, committed to a
  repository, or is being used from unfamiliar IP addresses or Regions.
- Triaging unexpected resource creation, IAM privilege escalation, or billing
  spikes that suggest credential compromise.

This skill **investigates and recommends only**. It never mutates, deletes,
stops, or quarantines any resource. A human engineer reviews the findings and
runs every action.

## Purpose and operating rules

Use this skill when a long-term IAM access key (`AKIA...`), a temporary STS
credential (`ASIA...`), or an IAM principal's credentials are suspected to be
exposed or compromised, and you must determine what the credential did and what
a human should do to contain it.

**Non-negotiable operating rules — read first:**

1. **Investigate and recommend only. Never take action.** Do not deactivate
   keys, attach or detach policies, stop or terminate instances, modify
   security groups, change bucket policies, revoke sessions, or delete anything.
   Every containment, eradication, and recovery step is emitted as a
   *recommendation* for a human engineer to review and execute.
2. **Read-only tools only.** Use describe/list/get/lookup style calls to gather
   evidence (CloudTrail, IAM, Organizations/account metadata, resource
   describe calls, Cost Explorer). Do not call any mutating API.
3. **Evidence before action.** Every recommendation that stops, isolates, or
   deletes a resource MUST be preceded by an evidence-preservation step
   (snapshot, export, or version retention). Never recommend deleting the
   originally exposed key or the suspected-malicious resources before evidence
   is preserved — premature deletion destroys forensics and can affect
   eligibility for AWS account concessions.
4. **Containment applies to every resource regardless of environment.** Do not
   gate recommendations on production vs non-production. Produce
   quarantine/stop guidance for every questioned resource and clearly label the
   blast radius so the human can decide.
5. **Prefer the AWS-managed quarantine policy.** For a compromised IAM user,
   recommend attaching **`AWSCompromisedKeyQuarantineV3`** (the AWS-managed
   policy purpose-built for this) rather than a hand-written deny-all, and warn
   the engineer **not to remove it until remediation is fully complete**.
6. **Confirm legitimacy.** Always include a step to contact the credential
   owner to confirm whether the activity was authorized before concluding it is
   malicious — this is the documented false-positive path.

## Inputs you will receive

The pipeline passes a normalized incident payload (see
`references/detection-sources.md` for the raw event formats). Expect:

- `access_key_id` — the exposed key. `AKIA` = long-term IAM user key;
  `ASIA` = temporary STS credential (role or federated session).
- `username` / principal — may be absent; resolve it during investigation.
- `account_id`, `region`, `alert_source`
  (`aws_health` | `security_hub` | `trusted_advisor` | `manual`),
  `event_type`, and event time.
- Account context (name, owner/alternate contacts, tags) may be attached by the
  pipeline; if missing, gather it during Analyze.

If `access_key_id` is missing, investigate the principal and time window
instead, and note the gap in the report.

## Investigation lifecycle

Follow the AWS Security Incident Response lifecycle:
**Detect → Analyze → Contain → Eradicate → Recover → Post-incident.**
Produce output for every phase. Contain/Eradicate/Recover are *recommendations*.

---

### Phase 1 — Detect (confirm and scope the credential)

1. **Classify the credential type from the key prefix.**
   - `AKIA` → long-term customer-managed key tied to an IAM user (or, rarely,
     the account root user). It can be listed and deactivated in IAM.
   - `ASIA` → short-term STS credential. It **cannot** be deactivated in the
     IAM console. Containment is different: you revoke the underlying role's
     active sessions and/or disable the source principal. Examine the CloudTrail
     `userIdentity.sessionContext.sessionIssuer` element to find the role or
     user that issued the session.
   - Record which path applies — this drives the Contain recommendations.
2. **Resolve the owning principal.** If `username` is absent, identify the IAM
   user or role that owns `access_key_id` (list users / access keys, or read it
   from CloudTrail `userIdentity`).
3. **Record the detection source and its timestamp** (`detected_at`). Note that
   the true compromise time may be earlier than detection; treat `detected_at`
   as an upper bound and look earlier when activity suggests it.

### Phase 2 — Analyze (evidence gathering, read-only)

1. **Account context.** Capture account id, account name/alias, account
   **alternate contacts** (Operations/Security/Billing) and **tags** — these
   drive who is notified and provide ownership context.
2. **Principal permissions.** Review the principal's attached and inline
   policies and **IAM Access Advisor / last-accessed** data to understand blast
   radius (what the credential *could* do and what it *recently used*).
3. **Credential report.** Generate/read the **IAM credential report** to find
   other unrotated or unused keys on the same or related principals.
4. **CloudTrail activity — baseline vs post-compromise.**
   - Pull the credential's API history. Build a **historical baseline**
     (normal APIs, source IPs, Regions, cadence — up to ~90 days) and compare it
     to **post-compromise** activity.
   - Flag anomalies: **new API calls never seen in baseline, new source IPs,
     new Regions, request-volume spikes, and high-risk operations.** See
     `references/risk-scoring.md`.
   - For `ASIA`/role cases, inspect `sessionIssuer` and any `AssumeRole` chains
     for lateral movement.
5. **Classify every API call the credential made** using
   `references/api-classification.md` into: **Creates resource / Modifies
   resource / Grants access / Exfiltrates data / Read-only.** This determines
   the containment recommendation type per action (quarantine vs revert vs
   revoke).
6. **Discover the attack chain (resource discovery).**
   - Enumerate resources **created** by the credential (EC2, security groups,
     IAM users/roles/keys, S3, Lambda, RDS, ECS, and others).
   - Follow transitive creation: if the credential created an IAM
     user/role, discover what *that* principal created.
   - **Hunt for persistence/backdoors** even when nothing was "created":
     new login profiles, new access keys minted for other users, attached/put
     policies, changed role trust policies, opened security-group ingress,
     public S3 bucket policies/ACLs, new KMS grants, added Lambda permissions or
     updated function code, cross-account replication, new IAM
     users/roles/SAML/OIDC providers. See `references/api-classification.md`
     "high-risk modification & grant APIs."
7. **Search ALL Regions, not just the event Region.** Attackers commonly spin
   up resources (crypto-mining EC2) in Regions you do not normally use.
   CloudTrail `LookupEvents` is effectively global for management events; note
   that resource describe calls and containment are Region-specific.
8. **Billing / cost signal.** Check for unexpected cost or usage spikes (Cost
   Explorer / billing) that indicate crypto-mining or data-transfer abuse.
9. **Assign a risk level** (CRITICAL / HIGH / MEDIUM / LOW) using
   `references/risk-scoring.md`.

### Phase 3 — Contain (recommendations — human runs these)

Produce specific, copy-pasteable recommendations. For each, give **both AWS CLI
and Console** steps, the **blast radius**, and the **evidence-first** note.
Full per-resource steps are in `references/remediation-playbook.md`.

Order the containment recommendations:

1. **Neutralize the credential.**
   - `AKIA`: recommend **rotate → deactivate → verify app → (later) delete**,
     never delete first, and recommend attaching
     **`AWSCompromisedKeyQuarantineV3`** to the IAM user. Keep the exposed key
     for forensics until remediation is complete.
   - `ASIA`/role: recommend **revoking the role's active sessions** (attach an
     `aws:TokenIssueTime` deny statement / "Revoke active sessions"), and
     disabling/rotating the issuing principal. Deactivating a key does not stop
     an already-issued STS session.
2. **Quarantine created resources (evidence-first):** snapshot/preserve, then
   stop/isolate — EC2 (isolate SG + stop), security groups (revoke rules), S3
   (block public access + deny policy, preserve versions), Lambda (concurrency
   0 + remove permissions), RDS (snapshot + isolate + stop), ECS (stop task).
3. **Revert modifications & revoke granted access:** detach attacker-added
   policies, remove added SG ingress, remove Lambda permission statements,
   delete attacker-created login profiles/keys/users/roles (after evidence),
   revoke KMS grants, remove public bucket policies/ACLs.

Include a **confirm-with-owner** recommendation and a **suppress-false-positive**
note (if the owner confirms the activity was legitimate).

### Phase 4 — Eradicate (recommendations)

- After evidence is preserved and the owner confirms activity was unauthorized,
  recommend **deleting confirmed-malicious resources** and attacker-created
  principals/keys.
- Recommend **removing all attacker persistence** found in Analyze.
- Recommend **rotating any other credentials** the principal could reach and
  reviewing/reducing overly-permissive policies (least privilege).

### Phase 5 — Recover (recommendations)

- Recommend **restoring legitimate resources** from last known-good backups
  (EBS/RDS snapshots, S3 object versions) once the environment is clean.
- Recommend re-issuing replacement credentials via secure mechanisms (roles /
  short-lived credentials over long-term keys).
- Recommend verifying application functionality before deleting the original
  (now-deactivated) key.

### Phase 6 — Post-incident (recommendations)

- Recommend **root-user hygiene**: delete root access keys if any exist, enable
  **MFA on the root user**, verify account **primary/alternate contacts** and
  root email.
- Recommend **enabling or confirming** GuardDuty, Security Hub, and AWS Security
  Incident Response for ongoing detection.
- Recommend **escalation**: respond to the AWS Support case AWS auto-opens for
  exposed credentials, engage the customer's security team, and (for confirmed
  compromise) contact AWS Security. Provide a Sev-2-style summary.
- Recommend prevention: stop using long-term keys, adopt IAM Identity Center /
  roles, add SCP guardrails, add detective controls for the anomalous behavior
  observed.

---

## Output contract (produce EXACTLY this structure)

Return your findings in the following fixed Markdown structure so the
Investigation Callback Lambda can parse it. Also produce an **Artifact** (the
full report) and one **Recommendation** per questioned resource/action
(status: Proposed) when those capabilities are available.

```
# ACCESS KEY COMPROMISE INVESTIGATION

## SUMMARY
- Investigation ID: <id or "n/a">
- Account: <account_id> (<account_name>)
- Account Owner / Alternate Contacts: <emails or "unknown">
- Access Key: <access_key_id> (<AKIA long-term | ASIA temporary>)
- Principal: <iam_user_or_role or "unresolved">
- Detection Source: <aws_health|security_hub|trusted_advisor|manual>
- Detected At: <timestamp>
- Overall Risk: <CRITICAL|HIGH|MEDIUM|LOW>
- Confirmed Malicious: <yes|no|unconfirmed — pending owner confirmation>

## FINDINGS
- API calls analyzed: <n> across <n> services; <n> Regions
- Anomalies: <new APIs / new IPs / new Regions / volume spike / high-risk ops>
- Resources created: <count + short list>
- Modifications / access grants (persistence): <list or "none observed">
- Estimated cost/billing impact: <$ or "unknown">
- Notable IPs / Regions: <list>

## AFFECTED RESOURCES
| Resource | Type | Region | Activity | Suspicion | Recommended Action |
|---|---|---|---|---|---|
| ... | ... | ... | created/modified/grant | why | quarantine/stop/revert/revoke |

## RECOMMENDATIONS (human-run, evidence-first)
### Contain
1. <credential neutralization — AKIA or ASIA path>
2. <per-resource quarantine/stop, each with CLI + Console + evidence step>
### Eradicate
- <delete confirmed-malicious after evidence; remove persistence>
### Recover
- <restore from known-good; re-issue credentials>
### Post-incident
- <root/MFA/contacts; enable detection; escalation; prevention>

## NOTIFICATION ROUTING
- Severity: <CRITICAL|HIGH|MEDIUM|LOW>
- Contacts: <emails/channels or "default SNS fallback">

## NEXT HUMAN ACTIONS
- Confirm with credential owner: <who + what to ask>
- If legitimate: suppress the finding (do not remediate)
- If malicious: proceed with Contain → Eradicate → Recover above
```

## Reference material

- `references/detection-sources.md` — raw event formats for the 3 sources and
  how the key/principal is extracted.
- `references/api-classification.md` — Creates / Modifies / Grants / Exfil /
  Read-only classification and the high-risk persistence APIs to hunt for.
- `references/risk-scoring.md` — anomaly signals and the CRITICAL/HIGH/MEDIUM/
  LOW rubric.
- `references/remediation-playbook.md` — per-resource-type, evidence-first
  manual containment/revert steps in AWS CLI and Console form, including the
  AKIA vs ASIA credential paths and `AWSCompromisedKeyQuarantineV3`.
