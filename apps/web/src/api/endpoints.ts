import type {
  CreateReviewRequest,
  CreateRunRequest,
  CreateRunResponse,
  DemoRunSummary,
  DownloadResponse,
  HealthResponse,
  Report,
  Review,
  Run,
  Scenario,
  Snapshot,
} from "../types/contracts";
import type { ApiClient } from "./client";

export function createApiEndpoints(client: ApiClient) {
  return {
    health: () => client.get<HealthResponse>("/v1/health"),
    listDemoRuns: () => client.get<DemoRunSummary[]>("/v1/demo-runs"),
    getDemoRun: (runId: string) => client.get<Run>(`/v1/demo-runs/${runId}`),
    getDemoSnapshot: (runId: string) => client.get<Snapshot>(`/v1/demo-runs/${runId}/snapshot`),
    getDemoReport: (runId: string) => client.get<Report>(`/v1/demo-runs/${runId}/report`),
    getDemoDownload: (runId: string) => client.get<DownloadResponse>(`/v1/demo-runs/${runId}/download`),

    listScenarios: () => client.get<Scenario[]>("/v1/scenarios"),
    createRun: (request: CreateRunRequest, idempotencyKey: string) =>
      client.post<CreateRunResponse>("/v1/runs", request, {
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    getRun: (runId: string) => client.get<Run>(`/v1/runs/${runId}`),
    getSnapshot: (runId: string) => client.get<Snapshot>(`/v1/runs/${runId}/snapshot`),
    getReport: (runId: string) => client.get<Report>(`/v1/runs/${runId}/report`),
    getDownload: (runId: string) => client.get<DownloadResponse>(`/v1/runs/${runId}/download`),
    submitReview: (runId: string, request: CreateReviewRequest) =>
      client.post<Review>(`/v1/runs/${runId}/review`, request),
  };
}

export type ApiEndpoints = ReturnType<typeof createApiEndpoints>;
