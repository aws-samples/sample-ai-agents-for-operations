# Risk Scoring — Anomaly Signals and Severity Rubric

Score the incident by comparing **post-compromise** activity to the
credential's **historical baseline** (up to ~90 days of normal behavior). The
score sets the `Overall Risk` in the report and the notification severity.

## Anomaly signals (each contributes to the score)

| Signal | Severity weight | How to detect |
|---|---|---|
| High-risk operations (create/delete/attach/put/grant) after compromise | CRITICAL | Classify calls via `api-classification.md`; any Grants/Modifies/Creates |
| New API calls never seen in baseline | HIGH | `post_apis - baseline_apis` non-empty |
| New source IP addresses | HIGH | `post_ips - baseline_ips` non-empty |
| Resource-creation spike (> 2× baseline daily rate) | HIGH | Compare creation cadence |
| New Regions (esp. unused Regions) | MEDIUM | `post_regions - baseline_regions` non-empty |
| API-call volume spike (> 3× baseline daily rate) | MEDIUM | Compare call cadence |
| Billing/cost spike | MEDIUM–HIGH | Cost Explorer / billing; crypto-mining pattern |
| Heavy reconnaissance (`Describe*`/`List*` across many services) | MEDIUM | Breadth of read calls vs baseline |

## Scoring rubric

Assign points, then map to a level:

- CRITICAL signal present: **+10 each**
- HIGH signal present: **+5 each**
- MEDIUM signal present: **+2 each**
- Attack-chain depth > 3 levels: **+5**; depth > 1: **+3**
- Total discovered resources > 10: **+5**; > 5: **+3**
- More than 10 high-risk operations: **+5**; more than 5: **+3**

**Map total to Overall Risk:**

| Total score | Overall Risk |
|---|---|
| ≥ 20 | CRITICAL |
| 10–19 | HIGH |
| 5–9 | MEDIUM |
| < 5 | LOW |

## Notes

- **`ASIA`/role compromise** with cross-account `AssumeRole` or
  `GetFederationToken` should be treated as **at least HIGH** — temporary
  credentials plus lateral movement is high blast radius.
- **Any persistence finding** (new login profile, new keys for other users,
  trust-policy change, public S3, KMS grant) raises the floor to **HIGH**.
- A **detection-time vs actual-compromise-time gap** means some malicious
  activity may sit in the "baseline" window. If early baseline activity looks
  anomalous, widen the window and re-score; note the uncertainty in the report.
- The score guides humans; it does not authorize any automated action.
