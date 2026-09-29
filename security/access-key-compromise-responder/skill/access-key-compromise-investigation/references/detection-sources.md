# Detection Sources — Event Formats and Key Extraction

Three independent AWS detection sources trigger this workflow via EventBridge.
They are complementary, not redundant: a key may be flagged by Trusted Advisor
(exposed on GitHub) before GuardDuty sees it used, or AWS Health may fire when
AWS auto-applies a quarantine policy. The Event Router Lambda normalizes all of
them into the incident payload the skill consumes.

## Normalized incident payload (what the skill receives)

```json
{
  "alert_source": "aws_health | security_hub | trusted_advisor | manual",
  "event_type": "AWS_RISK_CREDENTIALS_EXPOSED | <finding type> | EXPOSED_ACCESS_KEYS | MANUAL",
  "access_key_id": "AKIA... or ASIA... (may be null)",
  "username": "iam-user-or-role (may be null)",
  "account_id": "123456789012",
  "region": "us-east-1",
  "event_time": "ISO-8601",
  "detected_at": "ISO-8601"
}
```

## 1. AWS Health — `AWS_RISK_CREDENTIALS_EXPOSED`

Fires when AWS detects an IAM key exposed on a public repository. AWS also
auto-applies a quarantine policy to the user.

- `source`: `aws.health`, `detail-type`: `AWS Health Event`
- `detail.service`: `RISK`, `detail.eventTypeCode`: `AWS_RISK_CREDENTIALS_EXPOSED`
- Key extraction: `detail.affectedEntities[].entityValue` (starts with `AKIA`),
  and `detail.eventMetadata.publicKey` / `.userName`. The description often
  repeats the key ID.

## 2. Security Hub (GuardDuty findings)

GuardDuty emits IAM finding types (e.g.
`CredentialAccess:IAMUser/CompromisedCredentials`,
`UnauthorizedAccess:IAMUser/*`) when compromised credentials are used. All
GuardDuty IAM findings carry resource type `AwsIamAccessKey`.

- `source`: `aws.securityhub`, `detail-type`: `Security Hub Findings - Imported`
- Key extraction: `detail.findings[].Resources[]` where `Type == AwsIamAccessKey`;
  parse the access key ID from `Id` (ARN or `AWS::IAM::AccessKey:<id>` forms);
  `Details.AwsIamAccessKey.UserName` for the principal; `Severity.Label` for
  severity.
- Filter to `RecordState = ACTIVE` and `Workflow.Status = NEW` to avoid
  reprocessing.

## 3. Trusted Advisor — Exposed Access Keys

- `source`: `aws.trustedadvisor`,
  `detail-type`: `Trusted Advisor Check Item Refresh Notification`
- `detail.check-name`: `Exposed Access Keys`, `detail.status`: `ERROR`/`WARN`
- Key extraction: `detail.check-item-detail."Access Key ID"` and
  `."IAM User"`.

## Testing note

EventBridge reserves the `aws.health`, `aws.securityhub`, and
`aws.trustedadvisor` sources, so you cannot publish synthetic events onto the
bus. Test by invoking the **Event Router Lambda directly** with the sample
files in `events/`, which drives the same downstream flow a real event would.
