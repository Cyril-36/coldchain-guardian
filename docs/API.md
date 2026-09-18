# HTTP API

ColdChain Guardian uses a framework-free Python Lambda handler behind API Gateway HTTP API.
The routes operate on synthetic telemetry only. They do not make medical disposition or product
release decisions.

The contract version is `1.0`, JSON fields use `snake_case`, and all private routes are under
`/v1`. API Gateway validates Cognito access tokens before invoking the Lambda. The handler reads
only verified claims from `requestContext.authorizer.jwt.claims`; it never decodes a browser token.

## Access model

- `/v1/health` and `/v1/demo-runs/*` are public.
- `/v1/scenarios` and `/v1/runs/*` require a verified, allowlisted operator subject.
- `POST /v1/runs` and `POST /v1/runs/{run_id}/review` additionally require the
  `coldchain/write` scope.
- Protected run reads return `404` for a missing run or a run owned by another operator.
- Every public subresource independently checks `is_public_demo=true`; knowing a private UUID is
  not enough to read it.
- Public run lists and details set `review` to `null`; operator identity and review notes are
  available only from the owner-authorized run route.
- The review actor is always taken from the verified JWT subject. A client-supplied actor is
  rejected as an unknown field.

Production configuration requires `ARTIFACT_BUCKET`, `RUNS_TABLE`, `INVESTIGATION_QUEUE_URL`, and
`ALLOWED_OPERATOR_SUBS`. `BUILD_SHA` is optional and is returned by health checks. CORS is an API
Gateway configuration owned by the infrastructure workstream; there is no runtime switch that
disables authentication.

## Routes

| Method | Route | Access | Result |
|---|---|---|---|
| GET | `/v1/health` | Public | Schema version and build SHA |
| GET | `/v1/demo-runs` | Public | At most five curated summaries |
| GET | `/v1/demo-runs/{run_id}` | Public curated | Public-safe run |
| GET | `/v1/demo-runs/{run_id}/snapshot` | Public curated | Validated synthetic snapshot |
| GET | `/v1/demo-runs/{run_id}/report` | Public curated | Validated report |
| GET | `/v1/demo-runs/{run_id}/download` | Public curated | Five-minute report URL |
| GET | `/v1/scenarios` | Operator | Public scenario catalogue |
| POST | `/v1/runs` | Operator write | `202` run reservation |
| GET | `/v1/runs/{run_id}` | Owner | Run and real stage events |
| GET | `/v1/runs/{run_id}/snapshot` | Owner | Frozen synthetic snapshot |
| GET | `/v1/runs/{run_id}/report` | Owner | Report, or `409` until ready |
| GET | `/v1/runs/{run_id}/download` | Owner | Five-minute report URL, or `409` |
| POST | `/v1/runs/{run_id}/review` | Owner write | Persisted review |

Known routes reject unsupported methods with `405`. All `GET` routes have no request body.

## Read examples

Health is public:

```http
GET /v1/health HTTP/1.1
```

```json
{"status":"ok","schema_version":"1.0","build_sha":"abc123"}
```

The protected scenario catalogue contains control-plane labels only. It never includes expected
causes, hypotheses, answers, or generator internals:

```http
GET /v1/scenarios HTTP/1.1
Authorization: Bearer <access-token>
```

```json
[
  {"scenario_id":"normal_control","label":"Normal control"},
  {"scenario_id":"door_exposure","label":"Door exposure"},
  {"scenario_id":"refrigeration_problem","label":"Refrigeration problem"},
  {"scenario_id":"sensor_disagreement","label":"Sensor disagreement"},
  {"scenario_id":"ambiguous_incident","label":"Ambiguous incident"}
]
```

An owner reads run state with `GET /v1/runs/{run_id}`. The public equivalent is
`GET /v1/demo-runs/{run_id}` and succeeds only for an explicitly curated run.

```http
GET /v1/runs/3687c2e2-2275-4f2a-9f5b-86bf079a950f HTTP/1.1
Authorization: Bearer <access-token>
```

```json
{
  "run_id":"3687c2e2-2275-4f2a-9f5b-86bf079a950f",
  "status":"queued",
  "stage":"preparing",
  "created_at":"2026-09-18T09:00:00Z",
  "completed_at":null,
  "report_id":null,
  "review":null,
  "generation_mode":null,
  "shipment_id":"e79dd330-9fd1-4ca4-afdd-09ec46f71111",
  "snapshot_id":"b3df257a-65f1-491f-b3e0-d1419ad093c6",
  "stage_events":[],
  "report_summary":null,
  "error":null,
  "is_public_demo":false
}
```

Snapshot routes are `GET /v1/runs/{run_id}/snapshot` for an owner and
`GET /v1/demo-runs/{run_id}/snapshot` for a curated demo. Both return the same validated,
frozen `Snapshot` shape. The real simulator response contains 46 readings per sensor; this compact
sample shows the record shape:

```json
{
  "snapshot_id":"b3df257a-65f1-491f-b3e0-d1419ad093c6",
  "shipment_id":"e79dd330-9fd1-4ca4-afdd-09ec46f71111",
  "schema_version":"1.0",
  "source":"simulated",
  "cutoff_at":"2026-09-18T09:45:00Z",
  "policy":{"policy_id":"illustrative-refrigerated-1","policy_version":"1.0","min_c":2.0,"max_c":8.0,"expected_interval_seconds":60.0,"max_gap_seconds":120.0},
  "sensors":[
    {"sensor_id":"662a8084-52dc-4e30-bcf8-d32ac69f0650","placement":"front_air","role":"reference"},
    {"sensor_id":"ea687d29-da4f-4a1c-aa0a-eecf364553d5","placement":"rear_air","role":"comparison"}
  ],
  "readings":[{"event_id":"bf848027-d88b-4174-ad21-c5e186de9533","sensor_id":"662a8084-52dc-4e30-bcf8-d32ac69f0650","observed_at":"2026-09-18T09:00:00Z","temperature_c":5.02}],
  "events":[]
}
```

Report routes are `GET /v1/runs/{run_id}/report` and
`GET /v1/demo-runs/{run_id}/report`. They return `409 report_not_ready` until a validated current
report exists. A compact normal-report response is:

```json
{
  "report_id":"c6394d25-55a6-4f5e-a57d-99f7e0392832",
  "run_id":"3687c2e2-2275-4f2a-9f5b-86bf079a950f",
  "snapshot_id":"b3df257a-65f1-491f-b3e0-d1419ad093c6",
  "snapshot_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "schema_version":"1.0",
  "detector_version":"1.0.0",
  "prompt_version":"1.0.0",
  "model_id":null,
  "created_at":"2026-09-18T09:46:00Z",
  "cutoff_at":"2026-09-18T09:45:00Z",
  "measurements":[],
  "outcome":"no_excursion",
  "primary_hypothesis":null,
  "hypotheses":[],
  "next_checks":[],
  "limitations":["Synthetic demonstration data."],
  "verification":{"status":"passed","errors":[],"warnings":[]},
  "generation_mode":"deterministic_only",
  "review_required":false,
  "evidence":[]
}
```

Download routes are `GET /v1/runs/{run_id}/download` and
`GET /v1/demo-runs/{run_id}/download`. The browser supplies only the run ID; it cannot submit an S3
key or object path. The successful response contains a narrow URL that expires after five minutes:

```json
{"url":"https://download.example.invalid/signed-report?expires=300"}
```

The public catalogue uses `GET /v1/demo-runs` and returns at most five curated summaries:

```json
[
  {
    "run_id":"3687c2e2-2275-4f2a-9f5b-86bf079a950f",
    "status":"completed",
    "stage":"ready",
    "created_at":"2026-09-18T09:00:00Z",
    "completed_at":"2026-09-18T09:46:00Z",
    "report_id":"c6394d25-55a6-4f5e-a57d-99f7e0392832",
    "review":null,
    "generation_mode":"deterministic_only",
    "is_public_demo":true,
    "label":"Normal synthetic demonstration"
  }
]
```

Every public detail, snapshot, report, and download request repeats the public-demo check. A
private run ID always returns `404` through these routes.

## Starting a run

The caller supplies a unique `Idempotency-Key` header (1–200 characters):

```http
POST /v1/runs HTTP/1.1
Authorization: Bearer <access-token>
Idempotency-Key: <unique-client-key>
Content-Type: application/json

{"scenario_id":"door_exposure","seed":917}
```

```json
{
  "run_id": "3687c2e2-2275-4f2a-9f5b-86bf079a950f",
  "status": "queued",
  "poll_url": "/v1/runs/3687c2e2-2275-4f2a-9f5b-86bf079a950f"
}
```

`seed` must be an integer when supplied; Boolean values are rejected. It may be omitted, in which
case the API creates a server-side seed. Run reservation atomically stores
the scenario, seed, fixed generator timestamp, snapshot ID, shipment ID, idempotency mapping, and
limits. The snapshot is generated and durably attached before the queue message is sent. The queue
message contains only `run_id`, `snapshot_id`, and `schema_version`.

Repeating the same operator, key, and normalized body returns the same run. Reusing the key with a
different body returns `409`. If queue delivery or a post-reservation storage operation fails, the
API returns retryable `503`; `x-run-id` identifies the already reserved run. Retry the original body
and key. A retry reuses the persisted seed and timestamp and can safely regenerate identical
telemetry. A queue send may be repeated if sending succeeded but the status update failed; worker
claim idempotency handles duplicate delivery.

## Reviews

```http
POST /v1/runs/3687c2e2-2275-4f2a-9f5b-86bf079a950f/review HTTP/1.1
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "decision": "request_more_evidence",
  "note": "Request the missing equipment log.",
  "report_id": "c6394d25-55a6-4f5e-a57d-99f7e0392832"
}
```

The note is limited to 1,000 characters. The report ID must be the run's current report. Reviews
are acknowledgements of evidence handling, not shipment release or proof of professional approval.
The response is the canonical persisted review:

```json
{
  "review_id":"63a3ee5c-ea69-4b92-b8d8-034f6f020142",
  "run_id":"3687c2e2-2275-4f2a-9f5b-86bf079a950f",
  "report_id":"c6394d25-55a6-4f5e-a57d-99f7e0392832",
  "decision":"request_more_evidence",
  "note":"Request the missing equipment log.",
  "actor_sub":"verified-operator-subject",
  "reviewed_at":"2026-09-18T09:50:00Z"
}
```

## Errors

Errors use one envelope and never contain stack traces, token data, raw AWS requests, or secrets:

```json
{
  "error": {
    "code": "report_not_ready",
    "message": "Report is not ready",
    "request_id": "api-gateway-request-id",
    "retryable": false
  }
}
```

The implemented status mapping is `401` unauthenticated, `403` forbidden, `404` missing or hidden,
`405` known route with an unsupported method, `409` state/idempotency/report conflict, `413`
oversized body, `422` invalid input, `429` run limit, and retryable `503` storage or queue failure.
Unexpected failures return a generic `500` response. Logs contain request IDs, response status, and
exception category only; request bodies, tokens, raw idempotency keys, provider responses, and
credentials are not logged.

## Verification and limitations

Offline API tests use the canonical memory storage adapter and an injected queue fake. The SQS
sender is tested for its exact message body and provider-error mapping. These checks do not prove
real API Gateway, Cognito, DynamoDB, S3, SQS, IAM, CORS, or presigned-URL behavior. Cyril must run
the cloud round trip in `docs/VERIFICATION.md` after integrating this Lambda with the SAM stack.

`StorageProtocol.get_public_run(run_id) -> Run | None` is the canonical narrow lookup for a
curated public run. Both current storage adapters implement it without scanning private runs.
