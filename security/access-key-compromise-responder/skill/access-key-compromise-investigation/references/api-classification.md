# API Classification — Creates / Modifies / Grants / Exfiltrates / Read-only

Use this to classify every CloudTrail API call the compromised credential made.
The classification drives the **type** of containment recommendation:

| Category | Meaning | Recommended action type |
|---|---|---|
| **Creates resource** | A new resource appears | Quarantine/stop the created resource (evidence-first) |
| **Modifies resource** | Existing resource changed | Revert the modification to pre-compromise state |
| **Grants access** | Permission/trust/policy broadened | Revoke the specific grant |
| **Exfiltrates data** | Read/copy/transfer of data | Cannot undo; block further access, preserve logs, assess disclosure |
| **Read-only** | No state change | Record for the report; no containment |

Attackers rarely only *create* resources — they **modify** and **grant** to
establish persistence and escalate. Hunt for the modification/grant APIs below
even when nothing was "created."

---

## Creates resource (quarantine the new resource)

- **EC2:** `RunInstances`, `CreateSecurityGroup`, `CreateKeyPair`,
  `ImportKeyPair`, `CreateVolume`, `CreateSnapshot`, `CreateImage`,
  `CreateNetworkInterface`, `CreateVpc`, `CreateSubnet`
- **IAM:** `CreateUser`, `CreateRole`, `CreatePolicy`, `CreateAccessKey`
- **S3:** `CreateBucket`
- **Lambda:** `CreateFunction`, `CreateFunction20150331`, `PublishLayerVersion`
- **RDS:** `CreateDBInstance`, `CreateDBSnapshot`
- **ECS/EKS:** `CreateCluster`, `CreateService`, `RunTask`
- **Other:** `CreateTable` (DynamoDB), `CreateTopic`/`CreateQueue` (SNS/SQS),
  `CreateStack` (CloudFormation), `CreateRepository` (ECR), `CreateSecret`,
  `PutParameter` (SSM), `CreateJob` (Glue), `CreateNotebookInstance`
  (SageMaker), `CreateInstances` (Lightsail)

## Modifies resource (revert the change)

- `ModifyInstanceAttribute`, `UpdateFunctionConfiguration`,
  `UpdateFunctionCode`, `ModifyDBInstance`, `PutBucketPolicy`, `PutBucketAcl`,
  `PutObjectAcl`, `PutBucketReplication` (cross-account exfil staging)

## Grants access (revoke the grant) — high-value persistence/escalation

- **IAM privilege escalation:** `AttachUserPolicy`, `AttachRolePolicy`,
  `AttachGroupPolicy`, `PutUserPolicy`, `PutRolePolicy`, `PutGroupPolicy`,
  `AddUserToGroup`, `UpdateAssumeRolePolicy`
- **New standing access:** `CreateLoginProfile`, `UpdateLoginProfile`,
  `CreateAccessKey` (for *other* users), `CreateUser`/`CreateRole` used for
  backdoor identities, `CreateSAMLProvider`, `CreateOpenIDConnectProvider`
- **Network backdoors:** `AuthorizeSecurityGroupIngress`,
  `AuthorizeSecurityGroupEgress`, `ModifyVpcEndpoint`
- **Lambda:** `AddPermission`, `CreateEventSourceMapping`
- **KMS:** `CreateGrant`, `PutKeyPolicy`, `EnableKey`, `ScheduleKeyDeletion`
- **STS lateral movement:** `AssumeRole` (cross-account),
  `GetFederationToken`

## Exfiltrates data (block + assess disclosure)

- `GetObject` (bulk / unusual), `ListBuckets` + mass `GetObject`,
  `CreateSnapshot`/`ModifySnapshotAttribute` (share snapshot out),
  `ModifyImageAttribute` (share AMI out), `PutBucketReplication`,
  `CopyObject` to external buckets, large `SelectObjectContent`

## Read-only (record only)

- `Describe*`, `List*`, `Get*` (without bulk-data patterns),
  `GetCallerIdentity`. Note: heavy `Describe*`/`List*` reconnaissance across
  many services is itself an anomaly signal (see `risk-scoring.md`).

---

## Persistence / backdoor checklist (always hunt these)

Even if no resource was created, check whether the credential did any of:

- Created or updated a **login profile** on any user (console password).
- Minted **new access keys** for other users.
- Attached/put **policies** on users/roles/groups, or changed a **role trust
  policy** to allow an external account/principal.
- Created new **IAM users/roles**, or a **SAML/OIDC identity provider**.
- Opened **security-group ingress** to `0.0.0.0/0` or unknown CIDRs.
- Made an **S3 bucket public** (policy/ACL) or added **cross-account
  replication**.
- Added a **Lambda resource-based permission** or updated function code, or
  created an **event source mapping**.
- Created a **KMS grant** or modified a **key policy**.
- Assumed a role **cross-account** or generated a **federation token**.

Report each as a persistence finding with the exact revert/revoke step from
`remediation-playbook.md` §8.
