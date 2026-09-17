# Shared implementation contracts — v1

Leader owns this contract. Both teammates should implement exactly this boundary and request changes through a PR. This document specifies the contract; it is not a generated OpenAPI schema. First implementation task: create Pydantic definitions, JSON Schema/OpenAPI, TypeScript types and validated examples from it.

## Common rules

- JSON field names use snake_case; timestamps are ISO 8601 UTC ending in Z. UI can display IST with an explicit timezone label.
- IDs are opaque UUIDs, not cause labels. All records contain `schema_version: "1.0"`.
- Temperatures use Celsius, seconds use numeric values, missing values use null. No strings such as `"12.1°C"` in numeric fields.
- Strictly reject unknown input fields, NaN/infinity, invalid timestamps, oversized payloads and conflicting duplicate event IDs.
- Every evidence item belongs to one snapshot. Snapshot bytes have a SHA-256 digest and immutable S3 key. SHA-256 demonstrates byte identity, not sensor authenticity.
- User-visible records never contain hidden scenario truth. Deployment excludes `eval/` and its labels.

## Snapshot

Fields:

| Field | Type / requirement |
|---|---|
| snapshot_id, shipment_id | UUID strings |
| schema_version | `1.0` |
| source | `simulated` in MVP |
| cutoff_at | Latest time the investigator may consider |
| policy | policy_id, policy_version, min_c, max_c, expected_interval_seconds, max_gap_seconds |
| sensors | List of sensor_id, placement, role (`reference` or `comparison`); exactly one configured reference |
| readings | List of event_id, sensor_id, observed_at, temperature_c |
| events | List of event_id, observed_at, event_type, value, source |

Illustrative policy: min 2, max 8, readings every 60 seconds, max gap 120 seconds. This is a simulated shipment policy, not a universal medicine requirement.

Event types: `door_state` with `open/closed/unknown`; `refrigeration_state` with `running/stopped/fault/unknown`; `vehicle_state` with `moving/stopped/unknown`. `running` is a control/status signal and does not prove cooling effectiveness. Unknown status must not be converted to “normal”.

Limits: snapshot at most 2,000 readings, 200 events and 1 MiB serialized JSON. No freeform operator notes in the investigator's snapshot for MVP. Sort by event time and event ID after validation. Identical duplicate IDs may be deduplicated; conflicting duplicates fail validation. Out-of-cutoff records are rejected.

Scenario generation produces a 45-minute snapshot with two sensors. Generator scenario configuration stays in the control/API layer; worker messages and tools expose only opaque IDs and measurements. Public scenario names may be visible in the selection UI, but never included in model context, S3 keys returned to the model or tool descriptions.

## Measurements and detection

Detector is a pure function of snapshot and policy, with a version number.

1. Flag a sample when temperature is strictly below min or above max. Exactly 2 and 8 are in range for the example policy. Flag any sensor; distinguish reference excursion from comparison-only anomaly.
2. Report observed min/max from finite samples. Do not invent product-core temperatures from air readings.
3. For consecutive samples of the same sensor separated by at most max_gap_seconds, use linear interpolation to estimate threshold crossings. Integrate the out-of-range portion of each valid interval.
4. For wider gaps, do not interpolate. Record `unknown_duration_seconds` and disjoint intervals. State the duration as an estimate over observed coverage, never as an exact total exposure.
5. If the first/last sample is outside range, mark the interval left/right-censored; do not extrapolate outside the snapshot.
6. Report `first_observed_out_at`, `last_observed_out_at`, `estimated_out_of_range_seconds`, `unknown_duration_seconds`, `sample_count`, `observed_min_c`, `observed_max_c`, `censored_start`, `censored_end`, `coverage_status` (`full`, `partial`, `unknown`) per sensor.
7. A normal control returns `no_excursion` and skips Bedrock. A sensor-only anomaly still requires review; normal comparison data is not proof that the reference or product is safe.
8. If a dataset contains multiple separate excursion windows beyond the MVP's single-window assumption, return `needs_review` with `multiple_windows_unsupported`; do not collapse them into one invented incident.

Each metric links to the readings used. Backend owns rounding (seconds to 1 decimal, temperature to 2 decimals); UI only formats.

## Evidence and report

EvidenceRef: `evidence_id`, `snapshot_id`, `kind` (`reading/event/derived_metric/policy`), `record_ids`, `observed_at` or interval, `summary`. Derived evidence additionally includes `method_version` and the input record IDs. Tools return these references alongside values.

Report fields:

- report_id, run_id, snapshot_id, snapshot_sha256, schema_version, detector_version, prompt_version, model_id (nullable for deterministic-only), created_at, cutoff_at.
- measurements: deterministic per-sensor metrics.
- outcome: `hypothesis_supported`, `unresolved`, or `no_excursion`.
- primary_hypothesis: `door_exposure`, `refrigeration_problem`, `sensor_disagreement`, or null.
- hypotheses: each has the same hypothesis enum, assessment (`supported/contradicted/insufficient`), supporting_evidence_ids, conflicting_evidence_ids, missing_evidence and a short explanation.
- next_checks: structured objects with `action_code` (allowed: `inspect_door`, `check_refrigeration`, `verify_sensor`, `request_missing_logs`, `quality_review`; accepts alias `code`), `reason` (string), and `evidence_ids` (list of UUIDs; accepts alias `related_evidence_ids`).
- limitations: strings, including simulation and any data gaps.
- verification: status (`passed/blocked`), errors and warnings. “Passed” means schema/numeric/reference checks passed; it does not certify the cause.
- generation_mode: `bedrock` or `deterministic_only`.
- review_required: always true for an anomaly; false for a normal control does not certify product viability.

The LLM outputs only a proposed hypothesis structure. The backend injects verified metrics, provenance and final status. Report contains no “safe to use”, release/discard instruction, calibrated probability or invented facts. Render unsupported narrative as blocked, not as a finding with a tiny warning.

## State machine

Internal/public status values: `pending_enqueue`, `queued`, `running`, `completed`, `needs_review`, `failed`. `completed` means report generation completed, not shipment approved. Public `stage`: `preparing`, `detecting`, `collecting_evidence`, `comparing_hypotheses`, `verifying`, `ready`, `failed`.

API atomically reserves the idempotency mapping, limits and run in pending_enqueue with stage preparing, assigning stable snapshot/shipment IDs and a generator base timestamp. It then persists the snapshot and its reference on that same run. Queue send occurs only after the snapshot reference is durable and succeeds before HTTP 202. A repeated request resumes preparation with the same IDs, seed and timestamp. Worker can claim pending_enqueue or queued because it may receive before the API's queued update. API must never overwrite running/terminal state with queued. Completion requires validated artifact persistence first. Lease owner uses conditional writes so a stale worker cannot overwrite a later attempt.

Known model errors, exhausted model budgets or blocked hypotheses finish `needs_review` with deterministic measurements and an explicit limitation. Infrastructure/storage failures retry and eventually fail. Process stages derive from execution events, not frontend timers.

## HTTP endpoints

Base: `/v1`. Error shape: `{ "error": { "code": "...", "message": "...", "request_id": "...", "retryable": false } }`.

| Method and route | Access | Request / response |
|---|---|---|
| GET /health | Public | `{status:"ok", schema_version:"1.0", build_sha:"..."}`; no resource names/secrets |
| GET /demo-runs | Public | Curated public run summaries, at most 5 |
| GET /demo-runs/{run_id} | Curated public only | Same public-safe run response as operator read |
| GET /demo-runs/{run_id}/snapshot | Curated public only | Synthetic snapshot |
| GET /demo-runs/{run_id}/report | Curated public only | Validated report |
| GET /demo-runs/{run_id}/download | Curated public only | Short-lived JSON artifact URL |
| GET /scenarios | Operator | List of scenario_id and human-readable label |
| POST /runs | Operator | `{scenario_id, seed}` + `Idempotency-Key`; 202 `{run_id,status,poll_url}` |
| GET /runs/{run_id} | Operator owner | Run status, stage, stage_events, timestamps, report summary, error, review and generation_mode |
| GET /runs/{run_id}/snapshot | Operator owner | Validated snapshot; no scenario truth |
| GET /runs/{run_id}/report | Operator owner | Full validated report; 409 if not ready |
| GET /runs/{run_id}/download | Operator owner | Short-lived URL for report JSON, or 409 |
| POST /runs/{run_id}/review | Owner | `{decision:"acknowledged"|"request_more_evidence",note,report_id}`; 200 review |

Public access to non-curated/private run IDs returns 404. Public demo routes and protected operator routes are separate, as listed above. All public subresources enforce `is_public_demo=true`; all protected routes use API Gateway JWT authorisation and backend ownership checks. Frontend gets both paths from the frozen OpenAPI.

stage_events is a bounded list of up to 40 records: event_id, stage, tool_name (nullable), started_at, finished_at (nullable), status (started/completed/failed) and evidence_ids. Return real execution metadata only, never raw prompts or private reasoning. append_stage_event is a storage-interface method in addition to set_stage. A deterministic replay and a Bedrock investigation use the same event format but retain their distinct generation_mode.

Operator writes require Cognito access token, custom scope `coldchain/write`, and allowed team user. Protected reads require the operator token and ownership. No self-registration. Review actor comes from JWT subject; never trust a client actor field. Note maximum 1,000 characters. Acknowledgement is not product release or proof of professional QA approval.

Same user + same Idempotency-Key + same body returns the same run. Same key with a different body returns 409. Retain key mapping for 24 hours. Scope keys by user and hash key values in logs. Duplicate POSTs do not create additional paid work. 401 unauthenticated; 403 forbidden; 413 too large; 422 invalid; 429 daily/active-run cap; 503 temporary enqueue/storage failure.

Polling: 2 seconds while visible/running; back off to 5 seconds after 30 seconds. Stop at terminal state. Browser timeout shows “still processing” and offers refresh; it does not mark the backend failed.

## Storage interface

Leader publishes Protocol definitions (and later a memory implementation) before backend teammate begins AWS adapter integration:

```text
create_or_get_run(owner_sub, idempotency_key, request_hash, metadata) -> Run
attach_snapshot(run_id, snapshot_ref) -> None
put_snapshot(snapshot) -> ArtifactRef(key, sha256)
get_snapshot(snapshot_ref) -> Snapshot
get_run(run_id) -> Run | None
claim_run(run_id, attempt_id, now, lease_seconds) -> ClaimResult
set_stage(run_id, attempt_id, stage) -> None
append_stage_event(run_id, attempt_id, event) -> None
complete_run(run_id, attempt_id, status, report_ref, summary) -> None
put_report(report) -> ArtifactRef(key, sha256)
get_report(report_ref) -> Report
save_review(run_id, actor_sub, report_id, decision, note) -> Review
list_public_runs() -> list[RunSummary]
enqueue_run(run_id, snapshot_id, schema_version) -> None
```

In the state machine flow, `attach_snapshot(run_id, snapshot_ref)` explicitly records the durable snapshot artifact reference on the pending run prior to queue dispatch. In `claim_run`, `now` is a timezone-aware UTC datetime and `lease_seconds` is integer duration. `Review` contains `review_id`, `run_id`, `actor_sub`, `report_id`, `decision`, `note` (max 1,000 chars), and `created_at`.

Use one DynamoDB table: `RUN#uuid/META`, `RUN#uuid/REVIEW#uuid`, `IDEMP#user#hash/REQUEST`, `LIMIT#date/COUNT`, `ACTIVE#user/LEASE`, `PUBLIC/DEMO#run_id`. Define transactional writes for idempotency/caps and conditional writes for claims. No scans in request paths. Put telemetry in S3, not a giant DynamoDB item. S3 keys use opaque snapshot/report IDs; report objects are immutable.

Queue payload contains only run_id, snapshot_id, schema_version. On send failure retain pending_enqueue and return retryable 503. Repeating the same request retries enqueue without another run. Leader supplies a reconciliation command that requeues stale pending/expired jobs, checking active leases first. Duplicate queue messages are harmless. A crashed request must not leave a UI claiming successful completion.

## Example fixtures to publish first

Leader creates: queued-run.json, running-run.json, supported-report.json, unresolved-report.json, model-unavailable-report.json, normal-report.json, failed-run.json, door-snapshot.json. Every example is validated against Pydantic and generated TypeScript types. Frontend mock mode is visibly labeled and excluded from the release build.
