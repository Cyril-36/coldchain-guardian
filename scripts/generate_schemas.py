#!/usr/bin/env python3
"""Generate JSON Schema files and TypeScript types from canonical Pydantic models.

Outputs:
  contracts/schemas/*.schema.json
  contracts/generated/contracts.ts
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
backend_path = root / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from coldchain.contracts.schemas import (  # noqa: E402
    Report,
    Review,
    Run,
    Snapshot,
)


def generate_json_schemas() -> None:
    schemas_dir = root / "contracts" / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)

    models = [
        ("snapshot.schema.json", Snapshot),
        ("report.schema.json", Report),
        ("run.schema.json", Run),
        ("review.schema.json", Review),
    ]

    for filename, model_cls in models:
        target = schemas_dir / filename
        schema = model_cls.model_json_schema()
        target.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        print(f"  [OK] Generated {target.relative_to(root)}")


def generate_typescript_types() -> None:
    target_dir = root / "contracts" / "generated"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "contracts.ts"

    # TypeScript types conforming to docs/CONTRACTS.md and apps/web/src/types/contracts.ts
    content = '''export const SCHEMA_VERSION = "1.0" as const;

export type SchemaVersion = typeof SCHEMA_VERSION;
export type UUID = string;
export type ISODateTime = string;

export type RunStatus =
  | "pending_enqueue"
  | "queued"
  | "running"
  | "completed"
  | "needs_review"
  | "failed";

export type RunStage =
  | "preparing"
  | "detecting"
  | "collecting_evidence"
  | "comparing_hypotheses"
  | "verifying"
  | "ready"
  | "failed";

export type StageEventStatus = "started" | "completed" | "failed";
export type GenerationMode = "bedrock" | "deterministic_only";

export type EventType = "door_state" | "refrigeration_state" | "vehicle_state";
export type EvidenceKind = "reading" | "event" | "derived_metric" | "policy";
export type Outcome = "hypothesis_supported" | "unresolved" | "no_excursion";
export type Hypothesis =
  | "door_exposure"
  | "refrigeration_problem"
  | "sensor_disagreement";
export type HypothesisAssessment = "supported" | "contradicted" | "insufficient";
export type NextCheckCode =
  | "inspect_door"
  | "check_refrigeration"
  | "verify_sensor"
  | "request_missing_logs"
  | "quality_review";
export type ReviewDecision = "acknowledged" | "request_more_evidence";
export type CoverageStatus = "complete" | "partial" | "insufficient";

export interface Policy {
  policy_id: string;
  policy_version: string;
  min_c: number;
  max_c: number;
  expected_interval_seconds: number;
  max_gap_seconds: number;
}

export interface Sensor {
  sensor_id: UUID;
  placement: string;
  role: "reference" | "comparison";
}

export interface Reading {
  event_id: UUID;
  sensor_id: UUID;
  observed_at: ISODateTime;
  temperature_c: number;
}

export interface TelemetryEvent {
  event_id: UUID;
  observed_at: ISODateTime;
  event_type: EventType;
  value: string;
  source: string;
}

export interface Snapshot {
  snapshot_id: UUID;
  shipment_id: UUID;
  schema_version: SchemaVersion;
  source: "simulated";
  cutoff_at: ISODateTime;
  policy: Policy;
  sensors: Sensor[];
  readings: Reading[];
  events: TelemetryEvent[];
}

export interface SensorMeasurement {
  sensor_id: UUID;
  first_observed_out_at: ISODateTime | null;
  last_observed_out_at: ISODateTime | null;
  estimated_out_of_range_seconds: number;
  unknown_duration_seconds: number;
  sample_count: number;
  observed_min_c: number | null;
  observed_max_c: number | null;
  censored_start: boolean;
  censored_end: boolean;
  coverage_status: CoverageStatus;
  evidence_ids: UUID[];
}

export interface EvidenceInterval {
  start_at: ISODateTime;
  end_at: ISODateTime;
}

export interface EvidenceRef {
  evidence_id: UUID;
  snapshot_id: UUID;
  kind: EvidenceKind;
  record_ids: UUID[];
  observed_at?: ISODateTime;
  interval?: EvidenceInterval;
  summary: string;
  method_version?: string;
}

export interface HypothesisAssessmentRecord {
  hypothesis: Hypothesis;
  assessment: HypothesisAssessment;
  supporting_evidence_ids: UUID[];
  conflicting_evidence_ids: UUID[];
  missing_evidence: string[];
  explanation: string;
}

export interface NextCheck {
  code: NextCheckCode;
  reason: string;
  related_evidence_ids: UUID[];
}

export interface Verification {
  status: "passed" | "blocked";
  errors: string[];
  warnings: string[];
}

export interface Report {
  report_id: UUID;
  run_id: UUID;
  snapshot_id: UUID;
  snapshot_sha256: string;
  schema_version: SchemaVersion;
  detector_version: string;
  prompt_version: string;
  model_id: string | null;
  created_at: ISODateTime;
  cutoff_at: ISODateTime;
  measurements: SensorMeasurement[];
  outcome: Outcome;
  primary_hypothesis: Hypothesis | null;
  hypotheses: HypothesisAssessmentRecord[];
  next_checks: NextCheck[];
  limitations: string[];
  verification: Verification;
  generation_mode: GenerationMode;
  review_required: boolean;
  evidence: EvidenceRef[];
}

export interface StageEvent {
  event_id: UUID;
  stage: RunStage;
  tool_name: string | null;
  started_at: ISODateTime;
  finished_at: ISODateTime | null;
  status: StageEventStatus;
  evidence_ids: UUID[];
}

export interface Review {
  review_id: UUID;
  run_id: UUID;
  report_id: UUID;
  decision: ReviewDecision;
  note: string;
  actor_sub: string;
  reviewed_at: ISODateTime;
}

export interface RunSummary {
  run_id: UUID;
  status: RunStatus;
  stage: RunStage;
  created_at: ISODateTime;
  completed_at: ISODateTime | null;
  report_id: UUID | null;
  review: Review | null;
  generation_mode: GenerationMode | null;
}

export interface ReportSummary {
  outcome: Outcome;
  primary_hypothesis: Hypothesis | null;
  review_required: boolean;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id: string;
  retryable: boolean;
}

export interface Run extends RunSummary {
  shipment_id: UUID;
  snapshot_id: UUID;
  stage_events: StageEvent[];
  report_summary: ReportSummary | null;
  error: ApiErrorBody | null;
  is_public_demo?: boolean;
  label?: string;
}

export interface DemoRunSummary extends RunSummary {
  is_public_demo: true;
  label: string;
}

export interface CreateRunRequest {
  scenario_id: UUID;
  seed: number;
}

export interface CreateRunResponse {
  run_id: UUID;
  status: RunStatus;
  poll_url: string;
}

export interface Scenario {
  scenario_id: UUID;
  label: string;
}

export interface HealthResponse {
  status: "ok";
  schema_version: SchemaVersion;
  build_sha: string;
}

export interface DownloadResponse {
  url: string;
}

export interface CreateReviewRequest {
  decision: ReviewDecision;
  note: string;
  report_id: UUID;
}

export interface ApiErrorEnvelope {
  error: ApiErrorBody;
}
'''
    target.write_text(content, encoding="utf-8")
    print(f"  [OK] Generated {target.relative_to(root)}")


def main() -> int:
    print("Generating contract JSON Schemas...")
    generate_json_schemas()
    print("Generating TypeScript types...")
    generate_typescript_types()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
