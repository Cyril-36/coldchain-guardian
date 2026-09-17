#!/usr/bin/env node
/**
 * Verify all 8 JSON contract examples against the dashboard's actual Zod schemas.
 *
 * Source: apps/web/src/api/client.ts (from feat/dashboard)
 * Exits with code 0 if all 8 fixtures pass strict Zod validation; exits with 1 otherwise.
 */

const fs = require('fs');
const path = require('path');

// Resolve zod from local node_modules, npm exec / npx path, or global
function resolveZod() {
  const candidates = [
    'zod',
    path.resolve(__dirname, '../apps/web/node_modules/zod'),
    path.resolve(__dirname, '../node_modules/zod'),
  ];
  if (process.env.PATH) {
    const firstBin = process.env.PATH.split(':')[0];
    candidates.push(path.resolve(firstBin, '../zod'));
  }
  for (const candidate of candidates) {
    try {
      const mod = require(candidate);
      if (mod && mod.z) return mod.z;
    } catch (_) {}
  }
  // Try finding any zod in ~/.npm/_npx/
  try {
    const home = process.env.HOME || '/Users/cyril';
    const npxDir = path.join(home, '.npm', '_npx');
    if (fs.existsSync(npxDir)) {
      const subdirs = fs.readdirSync(npxDir);
      for (const subdir of subdirs) {
        const zodPath = path.join(npxDir, subdir, 'node_modules', 'zod');
        if (fs.existsSync(zodPath)) {
          const mod = require(zodPath);
          if (mod && mod.z) return mod.z;
        }
      }
    }
  } catch (_) {}
  throw new Error("Could not resolve 'zod'. Run with 'npx --yes -p zod node scripts/verify_client_fixtures.js' or npm install.");
}

const z = resolveZod();

// Schemas exactly as defined in apps/web/src/api/client.ts
const uuid = z.string().regex(/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/, "Expected valid UUID format");
const iso = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/, "Expected UTC timestamp in Z form");
const schemaVersion = z.literal("1.0");

const policy = z.object({
  policy_id: z.string(),
  policy_version: z.string(),
  min_c: z.number().finite(),
  max_c: z.number().finite(),
  expected_interval_seconds: z.number().finite(),
  max_gap_seconds: z.number().finite(),
}).strict();

const sensor = z.object({
  sensor_id: uuid,
  placement: z.string(),
  role: z.enum(["reference", "comparison"]),
}).strict();

const reading = z.object({
  event_id: uuid,
  sensor_id: uuid,
  observed_at: iso,
  temperature_c: z.number().finite(),
}).strict();

const telemetryEvent = z.object({
  event_id: uuid,
  observed_at: iso,
  event_type: z.enum(["door_state", "refrigeration_state", "vehicle_state"]),
  value: z.string(),
  source: z.string(),
}).strict();

const snapshotSchema = z.object({
  snapshot_id: uuid,
  shipment_id: uuid,
  schema_version: schemaVersion,
  source: z.literal("simulated"),
  cutoff_at: iso,
  policy,
  sensors: z.array(sensor),
  readings: z.array(reading),
  events: z.array(telemetryEvent),
}).strict();

const hypothesis = z.enum(["door_exposure", "refrigeration_problem", "sensor_disagreement"]);

const evidence = z.object({
  evidence_id: uuid,
  snapshot_id: uuid,
  kind: z.enum(["reading", "event", "derived_metric", "policy"]),
  record_ids: z.array(uuid),
  observed_at: iso.optional(),
  interval: z.object({ start_at: iso, end_at: iso }).strict().optional(),
  summary: z.string(),
  method_version: z.string().optional(),
}).strict();

const reportSchema = z.object({
  report_id: uuid,
  run_id: uuid,
  snapshot_id: uuid,
  snapshot_sha256: z.string(),
  schema_version: schemaVersion,
  detector_version: z.string(),
  prompt_version: z.string(),
  model_id: z.string().nullable(),
  created_at: iso,
  cutoff_at: iso,
  measurements: z.array(
    z.object({
      sensor_id: uuid,
      first_observed_out_at: iso.nullable(),
      last_observed_out_at: iso.nullable(),
      estimated_out_of_range_seconds: z.number().finite(),
      unknown_duration_seconds: z.number().finite(),
      sample_count: z.number().int().nonnegative(),
      observed_min_c: z.number().finite().nullable(),
      observed_max_c: z.number().finite().nullable(),
      censored_start: z.boolean(),
      censored_end: z.boolean(),
      coverage_status: z.enum(["complete", "partial", "insufficient"]),
      evidence_ids: z.array(uuid),
    }).strict()
  ),
  outcome: z.enum(["hypothesis_supported", "unresolved", "no_excursion"]),
  primary_hypothesis: hypothesis.nullable(),
  hypotheses: z.array(
    z.object({
      hypothesis,
      assessment: z.enum(["supported", "contradicted", "insufficient"]),
      supporting_evidence_ids: z.array(uuid),
      conflicting_evidence_ids: z.array(uuid),
      missing_evidence: z.array(z.string()),
      explanation: z.string(),
    }).strict()
  ),
  next_checks: z.array(
    z.object({
      code: z.enum(["inspect_door", "check_refrigeration", "verify_sensor", "request_missing_logs", "quality_review"]),
      reason: z.string(),
      related_evidence_ids: z.array(uuid),
    }).strict()
  ),
  limitations: z.array(z.string()),
  verification: z.object({
    status: z.enum(["passed", "blocked"]),
    errors: z.array(z.string()),
    warnings: z.array(z.string()),
  }).strict(),
  generation_mode: z.enum(["bedrock", "deterministic_only"]),
  review_required: z.boolean(),
  evidence: z.array(evidence),
}).strict();

const reviewSchema = z.object({
  review_id: uuid,
  run_id: uuid,
  report_id: uuid,
  decision: z.enum(["acknowledged", "request_more_evidence"]),
  note: z.string().max(1000),
  actor_sub: z.string(),
  reviewed_at: iso,
}).strict();

const runSummarySchema = z.object({
  run_id: uuid,
  status: z.enum(["pending_enqueue", "queued", "running", "completed", "needs_review", "failed"]),
  stage: z.enum(["preparing", "detecting", "collecting_evidence", "comparing_hypotheses", "verifying", "ready", "failed"]),
  created_at: iso,
  completed_at: iso.nullable(),
  report_id: uuid.nullable(),
  review: reviewSchema.nullable(),
  generation_mode: z.enum(["bedrock", "deterministic_only"]).nullable(),
}).strict();

const runSchema = runSummarySchema.extend({
  shipment_id: uuid,
  snapshot_id: uuid,
  stage_events: z.array(
    z.object({
      event_id: uuid,
      stage: z.enum(["preparing", "detecting", "collecting_evidence", "comparing_hypotheses", "verifying", "ready", "failed"]),
      tool_name: z.string().nullable(),
      started_at: iso,
      finished_at: iso.nullable(),
      status: z.enum(["started", "completed", "failed"]),
      evidence_ids: z.array(uuid),
    }).strict()
  ),
  report_summary: z.object({
    outcome: z.enum(["hypothesis_supported", "unresolved", "no_excursion"]),
    primary_hypothesis: hypothesis.nullable(),
    review_required: z.boolean(),
  }).strict().nullable(),
  error: z.object({
    code: z.string(),
    message: z.string(),
    request_id: z.string(),
    retryable: z.boolean(),
  }).strict().nullable(),
  is_public_demo: z.boolean().optional(),
  label: z.string().optional(),
}).strict();

const fixtures = [
  ['contracts/examples/door-snapshot.json', snapshotSchema, 'Snapshot'],
  ['contracts/examples/queued-run.json', runSchema, 'Run'],
  ['contracts/examples/running-run.json', runSchema, 'Run'],
  ['contracts/examples/failed-run.json', runSchema, 'Run'],
  ['contracts/examples/supported-report.json', reportSchema, 'Report'],
  ['contracts/examples/unresolved-report.json', reportSchema, 'Report'],
  ['contracts/examples/model-unavailable-report.json', reportSchema, 'Report'],
  ['contracts/examples/normal-report.json', reportSchema, 'Report'],
];

console.log("Validating 8 contract example fixtures against actual dashboard Zod client schemas...");
let failed = 0;
for (const [relPath, schema, schemaName] of fixtures) {
  const fullPath = path.resolve(__dirname, '..', relPath);
  if (!fs.existsSync(fullPath)) {
    console.error(`  [MISSING] ${relPath}`);
    failed++;
    continue;
  }
  const raw = fs.readFileSync(fullPath, 'utf8');
  let data;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    console.error(`  [JSON PARSE ERROR] ${relPath}: ${err.message}`);
    failed++;
    continue;
  }
  const result = schema.safeParse(data);
  if (!result.success) {
    console.error(`  [FAIL] ${relPath} (${schemaName})`);
    for (const issue of result.error.issues) {
      console.error(`         path: ${issue.path.join('.') || '<root>'} => ${issue.message}`);
    }
    failed++;
  } else {
    console.log(`  [OK] ${relPath} (passed ${schemaName})`);
  }
}

if (failed > 0) {
  console.error(`\nValidation failed: ${failed} issue(s) detected.`);
  process.exit(1);
} else {
  console.log(`\nAll ${fixtures.length} fixtures passed dashboard Zod client validation cleanly.`);
  process.exit(0);
}
