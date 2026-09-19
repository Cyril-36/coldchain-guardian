# Cyril’s work — investigation, verification and AWS deployment

You own the investigation engine: converting telemetry into defensible findings and proving the whole system works. You also own the AWS deployment and final integration. Harshith and Navadeep should be able to complete their assignments without designing your reasoning engine.

Read [master plan](../PLAN.md), [contracts](CONTRACTS.md), and [release gates](VERIFICATION.md).

## Your deliverables

1. Frozen shared schemas, interfaces and valid example responses.
2. Pure deterministic detector with independently checked arithmetic.
3. One Strands investigator with read-only evidence tools and Bedrock.
4. Deterministic report verifier and honest failure/degraded paths.
5. SQS worker with bounded work, leases and retry handling.
6. SAM deployment, operator authentication and cost controls.
7. Evaluation results, integrated release and recorded demonstration.

## Step 1 — unblock everyone, 60–90 minutes

- Confirm official kickoff has happened before project code starts. Record the actual submission deadline.
- Initialise the monorepo, gitignore, Python project, frontend location, CI skeleton and PR template. Do not rewrite an existing history.
- Create Pydantic v2 input/output models from CONTRACTS.md. Generate schemas and frontend types. Put examples in contracts/examples and validate them in CI.
- Publish storage Protocol interfaces; let Harshith implement them. You own semantics, not his adapter code.
- Publish a loopback-only local API harness using memory storage and a synthetic operator context, excluded from production packaging. This lets both teammates work without cloud accounts; follow TEAM-CLOUD-WORKFLOW.md.
- Test the selected Bedrock model from a local AWS profile, then from the deployed worker role. Test one tool call and structured output, measure latency and log the model ID and package versions.
- Lock versions and publish a shared .env.example containing names only. Backend: AWS_REGION, BEDROCK_MODEL_ID, RUNS_TABLE, ARTIFACT_BUCKET, INVESTIGATION_QUEUE_URL, ALLOWED_OPERATOR_SUBS, BUILD_SHA. (Earlier drafts of this list said TABLE_NAME and QUEUE_URL; the API, worker, infra/template.yaml and docs/API.md all use RUNS_TABLE and INVESTIGATION_QUEUE_URL, so those are the names. A daily run cap is enforced in storage rather than through a MAX_RUNS_PER_DAY variable.) Frontend receives only public IDs/URLs.

If the first model fails, distinguish access/region failure from model-quality failure. Fix the role/region or select one supported Bedrock model and rerun the same probe. Do not spend the weekend cycling models. If Bedrock remains unavailable, finish a clearly labeled deterministic AWS workflow and report the reduced AI scope.

## Step 2 — implement numerical authority

In backend/coldchain/core, build functions for validation, sorting/deduplication, per-sensor threshold detection, interpolation, coverage, censored intervals and evidence references. They must not import Bedrock, DynamoDB or Lambda types.

Use the exact rules in CONTRACTS.md. Preserve missingness and distinguish a configured reference sensor from a comparison sensor. A comparison-only spike is an anomaly worth investigation; it is not proof that the sensor is broken.

Create independent expected calculations before implementing convenience helpers. Example: readings at minute 0 = 5°C, 1 = 9°C, 2 = 9°C, 3 = 5°C with bounds 2–8 produce 90 seconds of estimated high-temperature duration under linear interpolation. The crossings are 0:45 and 2:15. Add below-range, exact-boundary, missing-gap and open-ended cases.

Generate deterministic facts such as door-open-before-rise, overlap interval and temperature recovery after close. Include underlying record IDs and method version. A temporal relationship supports a hypothesis but does not prove causation.

## Step 3 — build tools before prompts

Register only read-only tools bound to the current snapshot and run; do not allow the model to supply arbitrary S3 keys or query other runs.

| Tool | Returns | Invariant |
|---|---|---|
| get_excursion_summary | Verified per-sensor metrics and coverage | Numbers come from core code |
| get_sensor_comparison | Aligned readings and disagreement evidence | Never silently align across large gaps |
| get_door_events | Door timeline around the window | Missing is different from closed |
| get_refrigeration_events | Running/stopped/fault observations | Running does not mean effective cooling |
| get_vehicle_events | Stationary/moving context | Context alone is not causal proof |
| get_handling_policy | Exact configured policy and version | No general-purpose medical advice |

Tools return bounded structured data and evidence IDs. Cache tool responses within a run. Reject evidence beyond the snapshot cutoff. Store tool name, start/end time, status and returned evidence IDs for the visible execution trace. Do not log hidden reasoning or require chain-of-thought output.

## Step 4 — implement the investigator

Create one Strands Agent with explicit BedrockModel configuration and a Pydantic proposed-report schema. Initial context includes opaque run/snapshot IDs, event cutoff, available tools and the anomaly summary. It must not include scenario names, expected cause, fixture filenames or evaluator labels.

Instructions for the investigator:

1. Retrieve relevant evidence before proposing a cause.
2. Compare door exposure, refrigeration problem and sensor disagreement.
3. Look for conflicting evidence and alternatives, not just a matching event.
4. Return supported/contradicted/insufficient assessments and evidence IDs.
5. If observations cannot distinguish alternatives, choose unresolved and name the next missing observation.
6. Never decide product viability or fabricate a confidence percentage.

Set maximum 8 model invocations, 12 tool calls and 90 seconds total application time, including SDK/schema-repair retries. Implement counters/hooks compatible with the pinned SDK and verify them. At most one repair attempt is allowed for invalid final output within the total budget; then degrade safely.

The model adds evidence selection and explanation. If rules perform equally well on the evaluation, say so. Do not claim autonomous root-cause proof merely because tools were called.

## Step 5 — deterministic verifier

Do not build a second LLM that simply approves the first. Validate:

- Output conforms to the schema and allowed enum/action values.
- Every evidence ID exists in the current snapshot's tool-accessible registry.
- Referenced evidence belongs to the cited hypothesis and falls within cutoff.
- Any displayed numerical quantities match deterministic metrics; prefer templated numerical sentences so the model cannot introduce extra numbers.
- Each supported hypothesis satisfies minimum evidence prerequisites and includes contradictory evidence identified by the rule layer.
- Gaps/unknown states cannot be omitted from report limitations.
- The final output has no shipment release/discard action.

Minimum prerequisites are conservative guards, not proofs: door support needs recorded opening before/around rise and a relevant temporal pattern; refrigeration support needs actual fault/stopped observations or otherwise remains unresolved; sensor disagreement needs readings from two sensors with valid temporal alignment. Disagreement alone never becomes “confirmed faulty sensor”. Mixed support must remain unresolved when no additional evidence separates causes.

To reduce misleading narrative, have the model select typed findings and evidence IDs, then render verified facts using backend templates. Keep a short separately labeled hypothesis explanation. Schema and ID checks cannot prove every semantic statement true; test the reasoning and disclose that limitation.

On invalid report: keep the deterministic incident summary, set needs_review, display a clear reason and missing checks. Never pass through the rejected text as the primary conclusion.

## Step 6 — worker and AWS infrastructure

Create the SAM template for API Gateway, API Lambda, worker Lambda, DynamoDB, S3, SQS/DLQ, CloudWatch and Cognito. Connect Amplify Hosting separately to the approved frontend build. Use one backend CodeUri/package root so shared modules are included. Measure package size; use a Lambda layer only if needed.

Use separate IAM roles. API may access only its table, artifact prefix and queue. Worker may read snapshots, write reports/status and invoke the chosen Bedrock model/profile. Neither needs admin or Marketplace subscription permissions at runtime. Cognito client is public with no secret; managed login uses code flow with PKCE and a coldchain/write scope. Disable self-signup; provision the three operators.

Worker sequence: conditional lease claim → load/hash-check snapshot → compute measurements → skip model for normal case → tool investigation → verify → immutable report write → conditional terminal update. Include attempt_id in writes. A duplicate message for terminal work is acknowledged without a model call. Active-lease messages must not steal work; expired claims can be retried. Design the lease shorter than redelivery visibility and longer than maximum function time, e.g. 150 seconds.

When model access fails or times out within the application deadline, persist needs_review with deterministic metrics and generation_mode=deterministic_only. Infrastructure exceptions are retryable. For jobs exhausting retries, supply an operator reconciliation script to inspect the DLQ, set failed and release active-run leases. In the UI, stale runs show delayed status; do not invent terminal success. This small demo accepts operator reconciliation rather than adding another orchestration service.

Enforce daily caps and active-run limits atomically. Include a backend feature flag to disable new runs without disabling viewing completed reports. Add request/run IDs and duration to logs; retain no authorization headers or raw prompts.

Deployment sequence: SAM backend → CloudFormation outputs → frontend public environment config → Amplify deploy → update Cognito callback/logout URLs and CORS to exact Amplify origin → full smoke. Use a Vite SPA rewrite to index.html for dashboard routes. Document all commands actually used.

## Step 7 — evaluate and accept PRs

- Review Harshith's storage idempotency, enqueue recovery and owner checks.
- Review Navadeep's use of real statuses, evidence navigation and display of uncertainty.
- Run offline gates on merged main, then cloud E2E and bounded Bedrock evaluation.
- Verify baseline-vs-agent results using the same observable inputs and fixed holdout cases.
- Only tag release after all required gates in VERIFICATION.md pass.

Your own core PR should still be read by one teammate for integration assumptions and obvious defects, even though you remain final verifier. Being the leader should not mean your changes bypass review.

## Handoff to teammates

At contract freeze deliver: schemas/examples, API route list, ownership map, memory storage adapter, backend Python version, frontend public environment names, Cognito settings, deployment output format, and a sample unresolved report. At final integration deliver deployed API URL, allowed frontend origin, operator setup instructions and known limitations.

## Completion evidence

Store the tested git commit, dependency locks, sanitized deployment outputs, scenario seeds, evaluation CSV/JSON, measured latencies and final video script. Required gates: see VERIFICATION.md. Recommended next improvement: real domain feedback. Optional: live device ingestion after submission.
