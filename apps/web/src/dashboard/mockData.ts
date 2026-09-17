import type { Report, Run, Snapshot } from "../types/contracts";

export const demoRun: Run = {
  run_id: "00000000-0000-0000-0000-000000001042",
  status: "needs_review",
  stage: "ready",
  created_at: "2026-09-17T12:00:00Z",
  completed_at: "2026-09-17T12:04:30Z",
  report_id: "00000000-0000-0000-0000-000000002042",
  review: null,
  generation_mode: "deterministic_only",
  shipment_id: "00000000-0000-0000-0000-000000003048",
  snapshot_id: "00000000-0000-0000-0000-000000004048",
  stage_events: [],
  report_summary: {
    outcome: "hypothesis_supported",
    primary_hypothesis: "door_exposure",
    review_required: true,
  },
  error: null,
};

export const demoSnapshot: Snapshot = {
  snapshot_id: demoRun.snapshot_id,
  shipment_id: demoRun.shipment_id,
  schema_version: "1.0",
  source: "simulated",
  cutoff_at: "2026-09-17T14:32:00Z",
  policy: {
    policy_id: "CC-2",
    policy_version: "2.4",
    min_c: 2,
    max_c: 8,
    expected_interval_seconds: 60,
    max_gap_seconds: 300,
  },
  sensors: [
    { sensor_id: "00000000-0000-0000-0000-000000005001", placement: "Cargo A", role: "reference" },
    { sensor_id: "00000000-0000-0000-0000-000000005002", placement: "Cargo B", role: "comparison" },
  ],
  readings: Array.from({ length: 20 }, (_, index) => {
    const base = 5.4 + Math.sin(index / 2.7) * 1.1;
    const excursion = index >= 12 && index <= 16 ? (index - 11) * 1.05 : 0;
    return {
      event_id: `00000000-0000-0000-0000-0000000060${String(index).padStart(2, "0")}`,
      sensor_id: index % 2 === 0 ? "00000000-0000-0000-0000-000000005001" : "00000000-0000-0000-0000-000000005002",
      observed_at: new Date(Date.parse("2026-09-17T12:00:00Z") + index * 6 * 60_000).toISOString(),
      temperature_c: Number((base + excursion).toFixed(1)),
    };
  }),
  events: [
    {
      event_id: "00000000-0000-0000-0000-000000007001",
      observed_at: "2026-09-17T12:18:00Z",
      event_type: "door_state",
      value: "opened · 11 min",
      source: "simulated",
    },
    {
      event_id: "00000000-0000-0000-0000-000000007002",
      observed_at: "2026-09-17T13:02:00Z",
      event_type: "vehicle_state",
      value: "telemetry gap · 4 min",
      source: "simulated",
    },
  ],
};

export const demoReport: Report = {
  report_id: demoRun.report_id!,
  run_id: demoRun.run_id,
  snapshot_id: demoRun.snapshot_id,
  snapshot_sha256: "synthetic-demo-digest",
  schema_version: "1.0",
  detector_version: "demo-detector",
  prompt_version: "demo-prompt",
  model_id: null,
  created_at: "2026-09-17T12:04:30Z",
  cutoff_at: demoSnapshot.cutoff_at,
  measurements: [
    {
      sensor_id: demoSnapshot.sensors[0].sensor_id,
      first_observed_out_at: "2026-09-17T13:12:00Z",
      last_observed_out_at: "2026-09-17T13:30:00Z",
      estimated_out_of_range_seconds: 1080,
      unknown_duration_seconds: 240,
      sample_count: 10,
      observed_min_c: 4.3,
      observed_max_c: 9.4,
      censored_start: false,
      censored_end: false,
      coverage_status: "partial",
      evidence_ids: [],
    },
  ],
  outcome: "hypothesis_supported",
  primary_hypothesis: "door_exposure",
  hypotheses: [
    {
      hypothesis: "door_exposure",
      assessment: "supported",
      supporting_evidence_ids: [],
      conflicting_evidence_ids: [],
      missing_evidence: [],
      explanation: "Door activity aligns with the observed excursion onset.",
    },
  ],
  next_checks: [
    { code: "inspect_door", reason: "Confirm door event logs.", related_evidence_ids: [] },
    { code: "check_refrigeration", reason: "Check refrigeration telemetry for the same interval.", related_evidence_ids: [] },
    { code: "verify_sensor", reason: "Compare sensor agreement.", related_evidence_ids: [] },
  ],
  limitations: ["Telemetry has a 4-minute gap, limiting duration estimation."],
  verification: { status: "passed", errors: [], warnings: [] },
  generation_mode: "deterministic_only",
  review_required: true,
  evidence: [],
};
