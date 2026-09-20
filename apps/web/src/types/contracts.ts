export const SCHEMA_VERSION = "1.0" as const;

export type SchemaVersion = typeof SCHEMA_VERSION;
export type UUID = string;
/**
 * A server-defined scenario identifier such as `normal_control` or
 * `door_exposure`. It is a name, not a UUID: the catalogue is owned by the API
 * and the client must pass the value through unchanged rather than construct one.
 */
export type ScenarioId = string;
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

export interface EvidenceRef {
  evidence_id: UUID;
  snapshot_id: UUID;
  kind: EvidenceKind;
  record_ids: UUID[];
  observed_at?: ISODateTime;
  interval?: {
    start_at: ISODateTime;
    end_at: ISODateTime;
  };
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

export interface Run extends RunSummary {
  shipment_id: UUID;
  snapshot_id: UUID;
  stage_events: StageEvent[];
  report_summary: Pick<Report, "outcome" | "primary_hypothesis" | "review_required"> | null;
  error: ApiErrorBody | null;
}

export interface DemoRunSummary extends RunSummary {
  is_public_demo: true;
  label: string;
}

export interface CreateRunRequest {
  scenario_id: ScenarioId;
  seed: number;
}

export interface CreateRunResponse {
  run_id: UUID;
  status: RunStatus;
  poll_url: string;
}

export interface Scenario {
  scenario_id: ScenarioId;
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

export interface ApiErrorBody {
  code: string;
  message: string;
  request_id: string;
  retryable: boolean;
}

export interface ApiErrorEnvelope {
  error: ApiErrorBody;
}
