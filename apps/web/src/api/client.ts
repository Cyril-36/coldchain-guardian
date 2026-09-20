import { z } from "zod";
import { ApiClientError, isApiErrorEnvelope } from "./errors";

export type TokenProvider = () => Promise<string | null>;

export interface ApiClientOptions { baseUrl: string; tokenProvider?: TokenProvider; fetchImpl?: typeof fetch; }
export interface RequestOptions extends Omit<RequestInit, "body"> { body?: unknown; }
function joinUrl(baseUrl: string, path: string): string { return `${baseUrl.replace(/\/$/, "")}/${path.replace(/^\//, "")}`; }
async function readBody(response: Response): Promise<unknown> { if (response.status === 204) return null; const text = await response.text(); if (!text) return null; try { return JSON.parse(text) as unknown; } catch { return text; } }

const uuid = z.string().regex(/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/, "Expected valid UUID format");
const iso = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/, "Expected UTC timestamp in Z form");
const schemaVersion = z.literal("1.0");
const policy = z.object({ policy_id: z.string(), policy_version: z.string(), min_c: z.number().finite(), max_c: z.number().finite(), expected_interval_seconds: z.number().finite(), max_gap_seconds: z.number().finite() }).strict();
const sensor = z.object({ sensor_id: uuid, placement: z.string(), role: z.enum(["reference", "comparison"]) }).strict();
const reading = z.object({ event_id: uuid, sensor_id: uuid, observed_at: iso, temperature_c: z.number().finite() }).strict();
const telemetryEvent = z.object({ event_id: uuid, observed_at: iso, event_type: z.enum(["door_state", "refrigeration_state", "vehicle_state"]), value: z.string(), source: z.string() }).strict();
const snapshotSchema = z.object({ snapshot_id: uuid, shipment_id: uuid, schema_version: schemaVersion, source: z.literal("simulated"), cutoff_at: iso, policy, sensors: z.array(sensor), readings: z.array(reading), events: z.array(telemetryEvent) }).strict();
const hypothesis = z.enum(["door_exposure", "refrigeration_problem", "sensor_disagreement"]);
const evidence = z.object({ evidence_id: uuid, snapshot_id: uuid, kind: z.enum(["reading", "event", "derived_metric", "policy"]), record_ids: z.array(uuid), observed_at: iso.optional(), interval: z.object({ start_at: iso, end_at: iso }).strict().optional(), summary: z.string(), method_version: z.string().optional() }).strict();
const reportSchema = z.object({ report_id: uuid, run_id: uuid, snapshot_id: uuid, snapshot_sha256: z.string(), schema_version: schemaVersion, detector_version: z.string(), prompt_version: z.string(), model_id: z.string().nullable(), created_at: iso, cutoff_at: iso, measurements: z.array(z.object({ sensor_id: uuid, first_observed_out_at: iso.nullable(), last_observed_out_at: iso.nullable(), estimated_out_of_range_seconds: z.number().finite(), unknown_duration_seconds: z.number().finite(), sample_count: z.number().int().nonnegative(), observed_min_c: z.number().finite().nullable(), observed_max_c: z.number().finite().nullable(), censored_start: z.boolean(), censored_end: z.boolean(), coverage_status: z.enum(["complete", "partial", "insufficient"]), evidence_ids: z.array(uuid) }).strict()), outcome: z.enum(["hypothesis_supported", "unresolved", "no_excursion"]), primary_hypothesis: hypothesis.nullable(), hypotheses: z.array(z.object({ hypothesis, assessment: z.enum(["supported", "contradicted", "insufficient"]), supporting_evidence_ids: z.array(uuid), conflicting_evidence_ids: z.array(uuid), missing_evidence: z.array(z.string()), explanation: z.string() }).strict()), next_checks: z.array(z.object({ code: z.enum(["inspect_door", "check_refrigeration", "verify_sensor", "request_missing_logs", "quality_review"]), reason: z.string(), related_evidence_ids: z.array(uuid) }).strict()), limitations: z.array(z.string()), verification: z.object({ status: z.enum(["passed", "blocked"]), errors: z.array(z.string()), warnings: z.array(z.string()) }).strict(), generation_mode: z.enum(["bedrock", "deterministic_only"]), review_required: z.boolean(), evidence: z.array(evidence) }).strict();
const reviewSchema = z.object({ review_id: uuid, run_id: uuid, report_id: uuid, decision: z.enum(["acknowledged", "request_more_evidence"]), note: z.string().max(1000), actor_sub: z.string(), reviewed_at: iso }).strict();
const runSummarySchema = z.object({ run_id: uuid, status: z.enum(["pending_enqueue", "queued", "running", "completed", "needs_review", "failed"]), stage: z.enum(["preparing", "detecting", "collecting_evidence", "comparing_hypotheses", "verifying", "ready", "failed"]), created_at: iso, completed_at: iso.nullable(), report_id: uuid.nullable(), review: reviewSchema.nullable(), generation_mode: z.enum(["bedrock", "deterministic_only"]).nullable() }).strict();
const runSchema = runSummarySchema.extend({ shipment_id: uuid, snapshot_id: uuid, stage_events: z.array(z.object({ event_id: uuid, stage: z.enum(["preparing", "detecting", "collecting_evidence", "comparing_hypotheses", "verifying", "ready", "failed"]), tool_name: z.string().nullable(), started_at: iso, finished_at: iso.nullable(), status: z.enum(["started", "completed", "failed"]), evidence_ids: z.array(uuid) }).strict()), report_summary: z.object({ outcome: z.enum(["hypothesis_supported", "unresolved", "no_excursion"]), primary_hypothesis: hypothesis.nullable(), review_required: z.boolean() }).strict().nullable(), error: z.object({ code: z.string(), message: z.string(), request_id: z.string(), retryable: z.boolean() }).strict().nullable(), is_public_demo: z.boolean().optional(), label: z.string().optional() }).strict();
const scenarioSchema = z.object({ scenario_id: uuid, label: z.string() }).strict();
const createRunResponseSchema = z.object({ run_id: uuid, status: z.enum(["pending_enqueue", "queued", "running", "completed", "needs_review", "failed"]), poll_url: z.string() }).strict();
const downloadResponseSchema = z.object({ url: z.string().url() }).strict();
const healthSchema = z.object({ status: z.literal("ok"), schema_version: schemaVersion, build_sha: z.string() }).strict();
const demoRunSummarySchema = runSummarySchema.extend({ is_public_demo: z.literal(true), label: z.string() }).strict();

function schemaFor(path: string, method: string) {
  if (path === "/v1/health") return healthSchema;
  if (path === "/v1/demo-runs") return z.array(demoRunSummarySchema);
  if (path.match(/^\/v1\/demo-runs\/[^/]+\/snapshot$/)) return snapshotSchema;
  if (path.match(/^\/v1\/demo-runs\/[^/]+\/report$/)) return reportSchema;
  if (path.match(/^\/v1\/demo-runs\/[^/]+\/download$/)) return downloadResponseSchema;
  if (path === "/v1/scenarios") return z.array(scenarioSchema);
  if (path === "/v1/runs" && method === "POST") return createRunResponseSchema;
  if (path.match(/^\/v1\/runs\/[^/]+\/snapshot$/)) return snapshotSchema;
  if (path.match(/^\/v1\/runs\/[^/]+\/report$/)) return reportSchema;
  if (path.match(/^\/v1\/runs\/[^/]+\/download$/)) return downloadResponseSchema;
  if (path.match(/^\/v1\/runs\/[^/]+\/review$/) && method === "POST") return reviewSchema;
  if (path.match(/^\/v1\/demo-runs\/[^/]+$/)) return runSchema;
  if (path.match(/^\/v1\/runs\/[^/]+$/)) return runSchema;
  return null;
}

export class ApiClient {
  private readonly baseUrl: string; private readonly tokenProvider?: TokenProvider; private readonly fetchImpl: typeof fetch;
  constructor(options: ApiClientOptions) { this.baseUrl = options.baseUrl; this.tokenProvider = options.tokenProvider; this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis); }
  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers = new Headers(options.headers); headers.set("Accept", "application/json"); if (options.body !== undefined) headers.set("Content-Type", "application/json");
    const token = this.tokenProvider ? await this.tokenProvider() : null; if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await this.fetchImpl(joinUrl(this.baseUrl, path), { ...options, body: options.body === undefined ? undefined : JSON.stringify(options.body), headers });
    const body = await readBody(response); if (!response.ok) throw new ApiClientError(response.status, isApiErrorEnvelope(body) ? body.error : null, typeof body === "string" ? body : undefined);
    const schema = schemaFor(path, options.method ?? "GET"); if (schema) { const result = schema.safeParse(body); if (!result.success) throw new Error(`Invalid API response for ${path}: ${result.error.issues[0]?.message ?? "schema validation failed"}`); return result.data as T; }
    return body as T;
  }
  get<T>(path: string, options?: Omit<RequestOptions, "method" | "body">): Promise<T> { return this.request<T>(path, { ...options, method: "GET" }); }
  post<T>(path: string, body: unknown, options?: Omit<RequestOptions, "method" | "body">): Promise<T> { return this.request<T>(path, { ...options, method: "POST", body }); }
}
