# Verification and release gates

Cyril owns the final acceptance decision. Each developer supplies test results for their own work. These are planned checks; none have been executed against an implementation yet.

## 1. Offline correctness gate

Implement and pass:

| Check | Required observation |
|---|---|
| Threshold boundaries | Exactly at min/max stays in range |
| Interpolated crossing | 5,9,9,5°C at 60-second intervals yields 90 seconds above 8°C |
| Low-temperature crossing | Below-min excursions detected independently |
| Gaps | No interpolation beyond max_gap_seconds; unknown coverage displayed |
| Censored start/end | No extrapolation beyond available readings |
| Duplicate and out-of-order data | Same-ID duplicates deduplicated; conflicts rejected; sorting deterministic |
| Secondary-only excursion | Sensor disagreement not lost because reference is normal |
| No excursion | Bedrock call count remains zero |
| Multiple windows | Explicit unsupported/needs_review outcome, no misleading combined duration |
| Invalid evidence ID | Report blocked and deterministic fallback displayed |
| Wrong snapshot/cutoff | References outside the authorized evidence rejected |
| Contradiction | Missing/fault evidence cannot be omitted from the final report |
| Model failure | Metrics and explicit AI-unavailable state remain usable |
| Model/tool budget | Invocation, tool and wall-clock limits actually stop processing |

Check generated schemas/examples and TypeScript types stay aligned. Separate numeric unit tests from agent-quality evaluation.

## 2. Data and API gate

- Same idempotency key/body produces one logical run. Changed body returns conflict.
- Concurrent requests cannot bypass daily/active-run caps.
- Queue-send failure can recover by repeating the original request.
- Duplicate SQS delivery does not duplicate model work after terminal completion.
- Worker crash permits safe lease expiry/retry; a stale attempt cannot overwrite a newer result.
- S3 failure cannot produce a completed status with a missing report.
- DLQ/stale-job reconciliation is demonstrated once, with retry limits respected.
- Unauthorized users cannot start paid runs or read private reports.
- Public endpoints serve only curated synthetic demos, including download paths.
- Review actor is server-derived and review references an existing current report.

## 3. Agent evaluation gate

Create 20 fixed holdout snapshots: 4 door, 4 refrigeration, 4 sensor disagreement, 4 ambiguous/missing/conflicting and 4 normal. Use independently constructed patterns beyond simply changing the random seed of the same templates. Freeze these before prompt tuning; tune on a separate development set. If holdouts are used for tuning, replace them and disclose the change.

Store expected labels/evidence in eval/ only. Worker packaging explicitly excludes this folder. The investigator receives the same observations as a simple rule baseline. Baseline rules: door temporal evidence → door hypothesis; explicit stopped/fault evidence with rising temperatures → refrigeration hypothesis; sensor disagreement → check sensor; conflicts or missing evidence → unresolved.

Measure:

- Supported-category agreement on the 12 clear synthetic incidents, with numerator/denominator.
- Unresolved rate on the 4 ambiguous cases; all must avoid unsupported certainty.
- No-excursion correctness and zero model calls on 4 normal cases.
- Citation validity, invented evidence, unsupported numerical claims and prohibited disposition actions.
- Model invocations, input/output tokens, end-to-end latency and structured-output failures.

Proposed release targets: at least 10/12 clear synthetic cases correctly categorised, 4/4 ambiguous cases unresolved, 4/4 normal cases correct, zero invalid evidence/numeric claims reaching the UI. These are engineering acceptance targets, not a claim of medical accuracy. If the agent misses the target, fix it, constrain it to explanation of rule-supported findings, or visibly ship the reduced deterministic mode.

Rerun one clear and one ambiguous case three times to check stability. Report every attempt. A small synthetic set does not establish field accuracy, calibrated confidence, prevented spoilage or time saved for real operators. The baseline comparison may show that AI adds no accuracy; report that honestly and assess explanation quality separately.

Save outputs with commit SHA, snapshot digest, dataset version, model ID, prompt version, SDK lock versions and seed. Evaluation code calculates results from outputs; no hand-edited score table.

## 4. AWS integration gate — Cyril runs this

1. Deploy from the checked-in SAM template and note the commit.
2. Verify Bedrock tool/structured-output probe using the actual worker role.
3. From the hosted UI create a fresh door run; confirm matching run ID in API, queue/worker logs and persisted report.
4. Open evidence links; compare values to the original snapshot.
5. Run ambiguous and sensor-disagreement cases and inspect limitations.
6. Temporarily use a controlled failing model configuration in a test stack/run; confirm deterministic degraded output. Restore configuration afterwards.
7. Repeat a POST with its original key; verify no additional completed model job.
8. Verify public signed-out demo and private-run access denial.
9. Review and download a report; check the export matches UI and provenance.
10. Refresh a deep link on Amplify and verify Cognito callback/logout.

Only Cyril needs AWS credentials for these steps. Teammates may use their app-level operator logins against the deployment. An application login grants no AWS console, billing or infrastructure permissions.

## 5. Commands to implement, then record actual results

Standardise these repo commands during scaffold creation:

```text
make test          offline Python and frontend tests
make lint          Ruff plus TypeScript/frontend checks
make dev-api       loopback-only development API with memory storage
make dev-web       Vite dashboard pointing to local API
make eval-offline  deterministic measurement/verifier/baseline checks
make eval-bedrock  explicitly enabled, bounded live-model evaluation
make build        SAM build and frontend production build
make smoke-cloud  operator-authorized cloud round-trip checks
```

These command names are targets for implementation, not existing executables. A clean clone must reproduce offline checks without AWS credentials. The development auth adapter is available only in the local harness and excluded from production packaging; there is no production environment variable that disables auth.

## 6. Three-minute demonstration

Aim for 2:40–2:50 to leave a margin under the official limit.

| Time | Show |
|---|---|
| 0:00–0:20 | User and problem: a quality reviewer assembling evidence after an excursion |
| 0:20–0:40 | Fresh simulated run submitted from the AWS-hosted dashboard |
| 0:40–1:20 | Actual processing, evidence retrieval, chart and supported hypothesis |
| 1:20–1:50 | Ambiguous example with missing evidence and appropriate unresolved result |
| 1:50–2:15 | Evidence click-through, review acknowledgement and report export |
| 2:15–2:40 | AWS architecture plus a matching run ID/log; what the team learned |
| 2:40–2:50 | Limitations, repo/live URL and concise outcome |

Do not expose account secrets, access tokens or personal information in the recording. Show real AWS behaviour and actual tool activity. If footage skips waiting time, label the cut/time compression rather than implying a faster execution.

## 7. Submission gate

- Confirm actual deadline from submission form/organisers; upload before D−3h.
- Public repo contains event-period history, README, setup, architecture, AWS services, evaluation and limitations.
- Credit libraries/assets and list AI coding tools used.
- Short writeup explains problem, implementation, AWS role, learning and measured behaviour.
- YouTube video is under three minutes, public/unlisted and opens signed out.
- Public app URL and curated scenarios work signed out; operator access is not required to inspect the delivered result.
- All three team members have completed required registration/check-in/profile steps.
- Verify form team details before submission; preserve the submitted commit and links.

Required blockers: any failed correctness, access, cloud-demo or submission gate. Recommended soon: independent domain feedback and real logger CSV validation. Optional non-blocking: additional visuals, device integration and extra agents.
