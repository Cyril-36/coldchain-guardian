# Harshith’s work — simulator, storage and API

Your task is to provide reliable data and predictable HTTP endpoints. Cyril owns the detector, AI, verifier and AWS infrastructure. You do not need to invent diagnosis rules or choose cloud services.

Read [master plan](../PLAN.md) and [shared contracts](CONTRACTS.md). Work in backend/coldchain/api, storage and simulator, with corresponding test directories. Branch: feat/data-api.

## Deliverable 1 — deterministic simulator

Implement a pure `generate_snapshot(scenario_id, seed)` function. Given the same seed and fixed base timestamp, it produces the same observation payload. API wraps it in new opaque snapshot/shipment IDs. Do not use wall-clock time inside the seeded measurement generator. Use 45 minutes, one-minute readings and two sensors.

Implement these cases in order:

| Scenario | Observations to generate | Important constraint |
|---|---|---|
| Normal control | Both sensors remain within the configured limits | No Bedrock call should be needed |
| Door exposure | Door opens, both temperatures rise with a lag, door closes and readings recover | Door timing varies by seed; no cause text in snapshot |
| Refrigeration problem | Door stays closed; refrigeration reports fault/stopped; both sensors rise | Equipment evidence must exist, not just a rising chart |
| Sensor disagreement | One sensor spikes; the other remains stable; no confirming equipment fault | Expected conclusion is disagreement/need to check, not certainty of failure |
| Ambiguous incident | Temperatures rise but door/equipment logs are missing or both causes are plausible | Unknown remains unknown |

Use bounded seeded noise that preserves each demonstration's intended observable pattern. Unit-test variation so noise does not accidentally remove the excursion. Keep the generator's labels/configuration separate from exported observations. Never attach expected_cause, scenario_id or “door failure” labels to the worker payload or model-facing snapshot.

Build a normalizer that validates sizes, timestamps, sensor IDs, duplicates and cutoff. Domain measurements remain the leader's responsibility.

Acceptance: same seed reproduces observations; all IDs resolve; timestamps obey cutoff; five distinct cases validate; scenario labels cannot be found in serialized investigator input. Clearly label all generated data as simulated.

## Deliverable 2 — storage adapters

Implement the leader's storage Protocol twice: in-memory for offline tests and AWS for production. Use boto3, DynamoDB and private S3. No PostgreSQL, MongoDB or extra ORM.

Implementation order:

1. Immutable S3 snapshot/report write and hash verification on read. Use opaque IDs, not scenario names, in object paths.
2. DynamoDB create/get run and append review. Persist only metadata and bounded summaries in DynamoDB.
3. Idempotency-key lookup scoped to operator, including request hash comparison.
4. Conditional state updates and run lease methods, following the leader's rules.
5. Atomic daily cap and per-operator active-run lease support. Expired leases can be replaced using a conditional check on time; TTL deletion alone is not a lock or an expiry guarantee.
6. Curated public demo index and report-download signing. Never accept arbitrary S3 paths from the browser.

If a transaction fails, distinguish expected conflicts from infrastructure errors. Do not catch every exception and return an empty dataset. Add tests for same-key duplicates, changed-body conflicts and a stale worker trying to overwrite a newer attempt.

Acceptance: one run has one logical snapshot/job per idempotency key; private data is not exposed by guessing a UUID; downloaded reports match the stored digest; duplicate completion cannot create contradictory current state.

## Deliverable 3 — API Lambda

Use plain Python Lambda handlers and Pydantic validation. Implement route dispatch without a web framework unless the leader explicitly changes the plan. Keep HTTP parsing separate from business functions.

Build in this order:

1. GET health and scenario catalogue.
2. POST runs: validate caller/body/key → enforce limits and establish idempotency → generate/persist snapshot → record pending_enqueue → enqueue → conditionally set queued → return 202.
3. Protected GET run, snapshot and report routes.
4. Public curated demo routes with the is_public_demo check on every resource.
5. GET download returning a five-minute presigned report URL after authorisation.
6. POST review; derive actor from verified token, bind the review to report_id and retain prior review entries.

Leader configures API Gateway JWT verification. Read identity from the verified request context; do not decode unverified browser JWTs yourself. Check run ownership inside the handler. Public read paths and protected operator paths are separate.

Avoid a partially successful POST pretending to work. If enqueue fails, return retryable 503 with the existing run_id when available. Repeating the original key retries the same pending job. If queue send succeeded but the response/update failed, repeat enqueue may occur; worker idempotency handles this. Cyril supplies the reconciliation operation for stale requests.

Handle CORS preflight through API Gateway, with the exact frontend origin. Return the agreed error shape and appropriate status code. Never leak a Python stack trace, AWS request body, token or secret to the UI.

## Deliverable 4 — tests and handoff

Write meaningful tests for:

- Every simulator case, deterministic seeding and forbidden label leakage.
- Invalid timestamps, unknown sensors, NaN/infinite readings, conflicting duplicates and oversize payloads.
- Repeat POSTs, same key/different body, storage failure before queue send and queue failure after persistence.
- Concurrent cap/lease claims and illegal state regression.
- Unauthenticated writes, owner mismatch and access to non-public demo IDs.
- Review actor spoofing and stale report_id review.
- Report-not-ready and expired download URL recovery.

Use fakes for unit tests. Run at least one real AWS round trip with the leader: submit → read status → retrieve snapshot → retrieve generated report → record review. A mocked boto3 test alone is not cloud integration evidence.

Create docs/API.md with sample requests/responses and docs/SIMULATOR.md with seed behaviour and limitations. Never include access tokens in examples.

## Suggested PRs

1. Simulator + validation + tests; no AWS dependency required.
2. Memory/AWS storage adapters + tests.
3. API routes + auth/ownership checks + enqueue failure behaviour.
4. Integration fixes and API documentation.

Each PR states changed contracts, tests and known gaps. Do not alter core diagnosis logic, prompts, UI or infrastructure. Ask the leader via the team channel when a schema is missing; propose a concrete field and example rather than silently adding it.

## Definition of done

All agreed routes work against frozen schemas; deterministic scenario generation and failure paths pass tests; cloud round trip passes; no direct model invocation exists in API code; documentation lets Navadeep use the API without reading your implementation.

Required: reliability and consistent responses. Recommended soon: CSV importer using the same schema. Optional: additional scenario families after required work passes.
