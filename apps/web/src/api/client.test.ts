import { describe, expect, it } from "vitest";
import { ApiClient } from "./client";
import { ApiClientError } from "./errors";

describe("ApiClient", () => {
  it("parses valid health response", async () => {
    const fetchImpl = async () =>
      new Response(JSON.stringify({ status: "ok", schema_version: "1.0", build_sha: "abc1234" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    const client = new ApiClient({ baseUrl: "https://api.coldchain.example.com", fetchImpl: fetchImpl as any });
    const health = await client.get<{ status: string }>("/v1/health");
    expect(health.status).toBe("ok");
  });

  it("handles ISO timestamps with microseconds in responses", async () => {
    const demoRunData = {
      run_id: "00000000-0000-0000-0000-000000001042",
      shipment_id: "00000000-0000-0000-0000-000000003048",
      snapshot_id: "00000000-0000-0000-0000-000000004048",
      status: "completed",
      stage: "ready",
      created_at: "2026-09-17T12:00:00.123456Z",
      completed_at: "2026-09-17T12:04:30.987654Z",
      report_id: "00000000-0000-0000-0000-000000002042",
      review: null,
      generation_mode: "deterministic_only",
      stage_events: [],
      report_summary: {
        outcome: "hypothesis_supported",
        primary_hypothesis: "door_exposure",
        review_required: true,
      },
      error: null,
      is_public_demo: true,
      label: "Public door demo",
    };

    const fetchImpl = async () =>
      new Response(JSON.stringify(demoRunData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    const client = new ApiClient({ baseUrl: "https://api.coldchain.example.com", fetchImpl: fetchImpl as any });
    const run = await client.get<any>("/v1/demo-runs/00000000-0000-0000-0000-000000001042");
    expect(run.run_id).toBe("00000000-0000-0000-0000-000000001042");
    expect(run.created_at).toBe("2026-09-17T12:00:00.123456Z");
  });

  it("throws ApiClientError with retryable flag when API returns structured error", async () => {
    const errorPayload = {
      error: {
        code: "CONCURRENCY_LIMIT",
        message: "Too many concurrent investigations",
        request_id: "req-999",
        retryable: true,
      },
    };

    const fetchImpl = async () =>
      new Response(JSON.stringify(errorPayload), {
        status: 429,
        headers: { "Content-Type": "application/json" },
      });

    const client = new ApiClient({ baseUrl: "https://api.coldchain.example.com", fetchImpl: fetchImpl as any });

    await expect(client.get("/v1/runs/some-id")).rejects.toThrow(ApiClientError);
    try {
      await client.get("/v1/runs/some-id");
    } catch (err: any) {
      expect(err).toBeInstanceOf(ApiClientError);
      expect(err.status).toBe(429);
      expect(err.retryable).toBe(true);
      expect(err.details.code).toBe("CONCURRENCY_LIMIT");
    }
  });
});
