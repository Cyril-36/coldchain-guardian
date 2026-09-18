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

## Starting a run

The caller supplies a unique `Idempotency-Key` header (1–200 characters):

```http
POST /v1/runs HTTP/1.1
Authorization: Bearer <operator access token>
Idempotency-Key: 6bf8ac79-55eb-4d6b-aea9-21bd5bf0d826
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

`seed` may be omitted. The API then creates a server-side seed. Run reservation atomically stores
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
Authorization: Bearer <operator access token>
Content-Type: application/json

{
  "decision": "request_more_evidence",
  "note": "Request the missing equipment log.",
  "report_id": "c6394d25-55a6-4f5e-a57d-99f7e0392832"
}
```

The note is limited to 1,000 characters. The report ID must be the run's current report. Reviews
are acknowledgements of evidence handling, not shipment release or proof of professional approval.

## Errors

Errors use one envelope and never contain stack traces, token data, raw AWS requests, or secrets:

```json
{
  "error": {
    "code": "report_not_ready",
    "message": "report is not ready",
    "request_id": "api-gateway-request-id",
    "retryable": false
  }
}
```

The implemented status mapping is `401` unauthenticated, `403` forbidden, `404` missing or hidden,
`409` state/idempotency/report conflict, `413` oversized body, `422` invalid input, `429` run limit,
and retryable `503` storage or queue failure. Unexpected failures return a generic `500` response.

## Verification and limitations

Offline API tests use the canonical memory storage adapter and an injected queue fake. The SQS
sender is tested for its exact message body and provider-error mapping. These checks do not prove
real API Gateway, Cognito, DynamoDB, S3, SQS, IAM, CORS, or presigned-URL behavior. Cyril must run
the cloud round trip in `docs/VERIFICATION.md` after integrating this Lambda with the SAM stack.
