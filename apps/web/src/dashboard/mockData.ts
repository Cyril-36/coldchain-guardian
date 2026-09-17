import type { Report, Run, Snapshot } from "../types/contracts";

const REFERENCE_SENSOR = "00000000-0000-0000-0000-000000005001";
const COMPARISON_SENSOR = "00000000-0000-0000-0000-000000005002";
const DOOR_EVENT = "00000000-0000-0000-0000-000000007001";
const GAP_EVENT = "00000000-0000-0000-0000-000000007002";
const DOOR_EVIDENCE = "00000000-0000-0000-0000-000000008001";
const PEAK_EVIDENCE = "00000000-0000-0000-0000-000000008002";
const GAP_EVIDENCE = "00000000-0000-0000-0000-000000008003";
const REFERENCE_READING = (minute: number) => `00000000-0000-0000-0000-${String(6000 + minute).padStart(12, "0")}`;
const COMPARISON_READING = (minute: number) => `00000000-0000-0000-0000-${String(7000 + minute).padStart(12, "0")}`;

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
  report_summary: { outcome: "hypothesis_supported", primary_hypothesis: "door_exposure", review_required: true },
  error: null,
};

const start = Date.parse("2026-09-17T12:00:00Z");
const readings = Array.from({ length: 46 }, (_, minute) => {
  if (minute >= 35 && minute <= 39) return [];
  const timestamp = new Date(start + minute * 60_000).toISOString();
  const normal = 5.2 + Math.sin(minute / 4) * 0.9;
  const excursion = minute >= 12 && minute <= 30 ? 3.0 + Math.sin((minute - 12) / 3) * 1.2 : 0;
  return [
    { event_id: REFERENCE_READING(minute), sensor_id: REFERENCE_SENSOR, observed_at: timestamp, temperature_c: Number((normal + excursion).toFixed(1)) },
    { event_id: COMPARISON_READING(minute), sensor_id: COMPARISON_SENSOR, observed_at: timestamp, temperature_c: Number((5.0 + Math.sin(minute / 5) * 0.7).toFixed(1)) },
  ];
}).flat();

const peak = readings.find((reading) => reading.event_id === REFERENCE_READING(30));
if (peak) peak.temperature_c = 9.4;
const low = readings.find((reading) => reading.event_id === REFERENCE_READING(4));
if (low) low.temperature_c = 4.3;

export const demoSnapshot: Snapshot = {
  snapshot_id: demoRun.snapshot_id,
  shipment_id: demoRun.shipment_id,
  schema_version: "1.0",
  source: "simulated",
  cutoff_at: "2026-09-17T12:45:00Z",
  policy: { policy_id: "CC-2", policy_version: "2.4", min_c: 2, max_c: 8, expected_interval_seconds: 60, max_gap_seconds: 300 },
  sensors: [
    { sensor_id: REFERENCE_SENSOR, placement: "Cargo A", role: "reference" },
    { sensor_id: COMPARISON_SENSOR, placement: "Cargo B", role: "comparison" },
  ],
  readings,
  events: [
    { event_id: DOOR_EVENT, observed_at: "2026-09-17T12:12:00Z", event_type: "door_state", value: "open", source: "simulated" },
    { event_id: GAP_EVENT, observed_at: "2026-09-17T12:40:00Z", event_type: "vehicle_state", value: "telemetry gap · 6 min", source: "simulated" },
  ],
};

const referenceReadings = demoSnapshot.readings
  .filter((reading) => reading.sensor_id === REFERENCE_SENSOR)
  .sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
const outOfRangeReadings = referenceReadings.filter((reading) => reading.temperature_c < demoSnapshot.policy.min_c || reading.temperature_c > demoSnapshot.policy.max_c);
const firstOut = outOfRangeReadings[0] ?? null;
const lastOut = outOfRangeReadings.at(-1) ?? null;
const observedMin = Math.min(...referenceReadings.map((reading) => reading.temperature_c));
const observedMax = Math.max(...referenceReadings.map((reading) => reading.temperature_c));
const excursionSeconds = firstOut && lastOut ? (Date.parse(lastOut.observed_at) - Date.parse(firstOut.observed_at)) / 1000 : 0;
const telemetryGapStart = Date.parse("2026-09-17T12:34:00Z");
const telemetryGapEnd = Date.parse("2026-09-17T12:40:00Z");
const unknownCoverageSeconds = (telemetryGapEnd - telemetryGapStart) / 1000;

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
  measurements: [{
    sensor_id: REFERENCE_SENSOR,
    first_observed_out_at: firstOut?.observed_at ?? null,
    last_observed_out_at: lastOut?.observed_at ?? null,
    estimated_out_of_range_seconds: excursionSeconds,
    unknown_duration_seconds: unknownCoverageSeconds,
    sample_count: referenceReadings.length,
    observed_min_c: observedMin,
    observed_max_c: observedMax,
    censored_start: false,
    censored_end: false,
    coverage_status: "partial",
    evidence_ids: [PEAK_EVIDENCE, GAP_EVIDENCE],
  }],
  outcome: "hypothesis_supported",
  primary_hypothesis: "door_exposure",
  hypotheses: [{
    hypothesis: "door_exposure",
    assessment: "supported",
    supporting_evidence_ids: [DOOR_EVIDENCE, PEAK_EVIDENCE],
    conflicting_evidence_ids: [],
    missing_evidence: ["Refrigeration telemetry for the excursion interval."],
    explanation: "The door-open event starts at the same observed minute as the reference-sensor excursion.",
  }],
  next_checks: [
    { code: "inspect_door", reason: "Confirm the recorded door-open event against door logs.", related_evidence_ids: [DOOR_EVIDENCE] },
    { code: "check_refrigeration", reason: "Check refrigeration telemetry for the same interval.", related_evidence_ids: [DOOR_EVIDENCE] },
    { code: "verify_sensor", reason: "Compare the reference sensor with the comparison sensor.", related_evidence_ids: [PEAK_EVIDENCE] },
  ],
  limitations: [`A six-minute telemetry gap occurs after the excursion and contributes ${unknownCoverageSeconds} seconds of unknown coverage.`],
  verification: { status: "passed", errors: [], warnings: [] },
  generation_mode: "deterministic_only",
  review_required: true,
  evidence: [
    { evidence_id: DOOR_EVIDENCE, snapshot_id: demoSnapshot.snapshot_id, kind: "event", record_ids: [DOOR_EVENT], observed_at: "2026-09-17T12:12:00Z", summary: "Door opened at excursion onset." },
    { evidence_id: PEAK_EVIDENCE, snapshot_id: demoSnapshot.snapshot_id, kind: "reading", record_ids: [REFERENCE_READING(30)], observed_at: "2026-09-17T12:30:00Z", summary: `Reference sensor reached ${observedMax}°C.` },
    { evidence_id: GAP_EVIDENCE, snapshot_id: demoSnapshot.snapshot_id, kind: "event", record_ids: [GAP_EVENT], observed_at: "2026-09-17T12:40:00Z", summary: "Telemetry gap recorded after the excursion." },
  ],
};
