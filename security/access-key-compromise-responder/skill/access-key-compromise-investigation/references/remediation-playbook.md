# Remediation Playbook — Manual, Evidence-First Containment

> **This is guidance for a human engineer.** The DevOps Agent and this skill
> never execute these steps. The agent copies the relevant steps into its
> **Recommendations** for a person to review and run. Every stop/isolate/delete
> step is **evidence-first**: preserve forensics before you change or destroy
> anything. Replace placeholders (`<...>`) with real values from the
> investigation. Set your Region per resource (`--region <region>`); attackers
> often use Regions you don't normally use.

---

## 0. Golden rules

1. **Preserve evidence before containment.** Snapshot EBS/RDS, retain S3
   versions, export logs. Do **not** delete the exposed key or suspected
   resources before evidence is captured — premature deletion destroys
   forensics and can affect eligibility for AWS account concessions.
2. **Rotate before you delete a key.** Create a replacement, deactivate the
   original, verify the app, and only then delete the original.
3. **Quarantine/stop, don't delete first.** Deletion is an *Eradicate*-phase
   action taken after evidence is preserved and the owner confirms malice.
4. **Confirm with the owner** whether the activity was authorized before
   concluding compromise (false-positive path).

---

## 1. Credential neutralization

### 1a. Long-term key (`AKIA...`) — IAM user key

**Recommended order: rotate → quarantine → deactivate → verify → delete.**

Preserve context first (who owns the key, last used):

```bash
aws iam get-access-key-last-used --access-key-id <AKIA...>
aws iam list-access-keys --user-name <username>
```

Attach the AWS-managed quarantine policy (limits blast radius immediately;
**do not remove it until remediation is complete**):

```bash
aws iam attach-user-policy \
  --user-name <username> \
  --policy-arn arn:aws:iam::aws:policy/AWSCompromisedKeyQuarantineV3
```

Create a replacement key (only if the workload legitimately needs one), then
**deactivate** (do not delete yet) the exposed key:

```bash
# aws iam create-access-key --user-name <username>   # if a replacement is needed
aws iam update-access-key \
  --user-name <username> \
  --access-key-id <AKIA...> \
  --status Inactive
```

Verify the application still works with the replacement. **Only after
verification and after evidence is preserved**, delete the original key:

```bash
# aws iam delete-access-key --user-name <username> --access-key-id <AKIA...>
```

**Console:** IAM → Users → `<username>` → Permissions → attach
`AWSCompromisedKeyQuarantineV3`; Security credentials → set the key
**Inactive**; delete only after verification.

> If the key is an **account root user** access key: there is almost never a
> legitimate reason for one. Preserve evidence, then **delete the root access
> key** and enable MFA on root (see §6).

### 1b. Temporary credential (`ASIA...`) — role / federated session

A temporary STS credential **cannot be deactivated** like an access key.
Deactivating a key does **not** stop an already-issued session. Instead:

Identify the issuing principal from CloudTrail
(`userIdentity.sessionContext.sessionIssuer`), then **revoke active sessions**
on the role by attaching a time-based deny (this is what the Console's
"Revoke active sessions" button does):

```bash
aws iam put-role-policy \
  --role-name <role_name> \
  --policy-name AWSRevokeOlderSessions \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Effect": "Deny",
      "Action": "*",
      "Resource": "*",
      "Condition": {"DateLessThan": {"aws:TokenIssueTime": "<ISO8601-now>"}}
    }]
  }'
```

**Console:** IAM → Roles → `<role_name>` → **Revoke active sessions**.

Also review and, if compromised, disable/rotate the **source principal** that
assumed the role (the IAM user/key/identity-provider session in
`sessionIssuer`). For federated users, coordinate with the identity provider
and, if applicable, an Organizations SCP to block re-assumption.

---

## 2. EC2 instance (created or hijacked)

**Evidence first** — snapshot volumes before stopping:

```bash
aws ec2 describe-instances --instance-ids <i-id> --region <region>
# snapshot each attached EBS volume
aws ec2 create-snapshot --volume-id <vol-id> --region <region> \
  --description "forensic-<i-id>-$(date +%s)"
```

Isolate (empty security group in the instance's VPC = no ingress/egress), then
stop (stop preserves the root volume for forensics; termination does not):

```bash
aws ec2 create-security-group --group-name quarantine-<i-id> \
  --description "quarantine <i-id>" --vpc-id <vpc-id> --region <region>
aws ec2 revoke-security-group-egress --group-id <quarantine-sg> --region <region> \
  --ip-permissions '[{"IpProtocol":"-1","IpRanges":[{"CidrIp":"0.0.0.0/0"}]}]'
aws ec2 modify-instance-attribute --instance-id <i-id> \
  --groups <quarantine-sg> --region <region>
aws ec2 stop-instances --instance-ids <i-id> --region <region>
```

Detach any IAM instance profile so the instance can't use its role:

```bash
aws ec2 describe-iam-instance-profile-associations \
  --filters Name=instance-id,Values=<i-id> --region <region>
aws ec2 disassociate-iam-instance-profile --association-id <assoc-id> --region <region>
```

**Console:** EC2 → snapshot volumes → attach an empty quarantine SG → detach IAM
role → Instance state → Stop.

---

## 3. EC2 security group (attacker-opened ingress / backdoor)

Capture the current rules (evidence), then revoke:

```bash
aws ec2 describe-security-groups --group-ids <sg-id> --region <region>
aws ec2 revoke-security-group-ingress --group-id <sg-id> --region <region> \
  --ip-permissions '<the exact ingress rules from describe>'
aws ec2 revoke-security-group-egress --group-id <sg-id> --region <region> \
  --ip-permissions '<the exact egress rules from describe>'
aws ec2 create-tags --resources <sg-id> --region <region> \
  --tags Key=Quarantine,Value=CompromisedAccessKey
```

**Console:** EC2 → Security Groups → edit inbound/outbound → remove the
attacker rules. If the whole SG was attacker-created, remove it from attached
ENIs first, then delete in the Eradicate phase.

---

## 4. S3 bucket (created or made public / exfil target)

**Preserve first:** enable versioning so nothing is silently overwritten, and
record current policy/ACL as evidence:

```bash
aws s3api get-bucket-policy --bucket <bucket> 2>/dev/null
aws s3api get-bucket-acl --bucket <bucket>
aws s3api put-bucket-versioning --bucket <bucket> \
  --versioning-configuration Status=Enabled
```

Block public access and apply a deny policy scoped to your account:

```bash
aws s3api put-public-access-block --bucket <bucket> \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

aws s3api put-bucket-policy --bucket <bucket> --policy '{
  "Version":"2012-10-17",
  "Statement":[{
    "Sid":"QuarantineDenyOutsideAccount",
    "Effect":"Deny","Principal":"*","Action":"s3:*",
    "Resource":["arn:aws:s3:::<bucket>","arn:aws:s3:::<bucket>/*"],
    "Condition":{"StringNotEquals":{"aws:PrincipalAccount":"<account_id>"}}
  }]
}'
```

**Console:** S3 → bucket → Permissions → enable Block Public Access → replace
policy with the deny → confirm versioning on. Review access logs / CloudTrail
data events for exfiltration.

---

## 5. Lambda function (created or code-injected)

Preserve the code/config first, then disable execution and remove any
resource-based permissions the attacker added:

```bash
aws lambda get-function --function-name <fn> --region <region>   # note CodeSha256
aws lambda get-policy --function-name <fn> --region <region> 2>/dev/null
# disable: no concurrency = cannot run
aws lambda put-function-concurrency --function-name <fn> \
  --reserved-concurrent-executions 0 --region <region>
# remove attacker-added permission statements by Sid
aws lambda remove-permission --function-name <fn> --statement-id <sid> --region <region>
```

**Console:** Lambda → function → Configuration → Concurrency → reserve **0** →
Permissions → remove unknown resource-based statements. Review event-source
mappings and `UpdateFunctionCode` history for injected code.

---

## 6. RDS instance (created or hijacked)

**Snapshot first** (evidence + recovery), then isolate and stop:

```bash
aws rds create-db-snapshot --db-instance-identifier <db> \
  --db-snapshot-identifier <db>-forensic-$(date +%s) --region <region>
# isolate: swap to an empty SG in the same VPC
aws rds modify-db-instance --db-instance-identifier <db> \
  --vpc-security-group-ids <quarantine-sg> --apply-immediately --region <region>
aws rds stop-db-instance --db-instance-identifier <db> --region <region>
```

**Console:** RDS → take a snapshot → Modify → set an empty security group →
Actions → Stop. Review DB logs for unauthorized access.

---

## 7. ECS task / service (attacker workload)

```bash
aws ecs list-tasks --cluster <cluster> --region <region>
aws ecs describe-tasks --cluster <cluster> --tasks <task-arn> --region <region>  # evidence
aws ecs stop-task --cluster <cluster> --task <task-arn> \
  --reason "compromised access key quarantine" --region <region>
# if backed by a service, scale it to 0 so it doesn't relaunch
aws ecs update-service --cluster <cluster> --service <svc> \
  --desired-count 0 --region <region>
```

**Console:** ECS → cluster → Tasks → Stop; Services → Update → desired tasks 0.

---

## 8. Revert modifications & revoke granted access (persistence removal)

For each item classified as **Modifies / Grants** in
`references/api-classification.md`:

- **Attacker-added IAM policies** — detach/delete:
  ```bash
  aws iam detach-user-policy --user-name <u> --policy-arn <arn>
  aws iam delete-user-policy --user-name <u> --policy-name <inline>
  aws iam detach-role-policy --role-name <r> --policy-arn <arn>
  aws iam delete-role-policy --role-name <r> --policy-name <inline>
  ```
- **Attacker-created login profile** (console access on a service account):
  ```bash
  aws iam delete-login-profile --user-name <u>
  ```
- **Attacker-minted access keys** on any user (preserve last-used evidence,
  then deactivate, later delete):
  ```bash
  aws iam update-access-key --user-name <u> --access-key-id <AKIA...> --status Inactive
  ```
- **Attacker-created IAM users/roles** — remove after evidence (Eradicate).
- **Changed role trust policy** — restore the legitimate
  `assume-role-policy-document`.
- **KMS grants** the attacker created — `aws kms revoke-grant`.
- **Public S3 policy/ACL** — see §4.
- **Opened security-group ingress** — see §3.

---

## 9. Eradicate (only after evidence preserved + owner confirms malicious)

- Delete confirmed-malicious resources (terminate quarantined EC2 after
  snapshot, delete rogue Lambda/S3/RDS after snapshot/version retention).
- Delete attacker-created principals, keys, login profiles, providers.
- Delete the original exposed key once the replacement is verified.

## 10. Recover

- Restore legitimate data from last known-good backups: EBS/RDS snapshots, S3
  object versions.
- Re-issue credentials via roles / short-lived credentials rather than new
  long-term keys where possible.

## 11. Post-incident hygiene

- **Root user:** delete any root access keys; enable **MFA on root**.
- **Account contacts:** verify primary and **alternate contacts**
  (Operations/Security/Billing) and root email.
- **Detection:** enable/confirm GuardDuty, Security Hub, and AWS Security
  Incident Response.
- **Escalation:** respond to the AWS Support case auto-opened for exposed
  credentials; engage the security team; for confirmed compromise, contact
  AWS Security (aws-security@amazon.com).
- **Prevention:** move off long-term keys (IAM Identity Center / roles), add
  SCP guardrails, add detective controls for the specific anomalous behavior
  observed, enforce 90-day rotation for any remaining keys.
