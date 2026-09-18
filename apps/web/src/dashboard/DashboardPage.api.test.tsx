import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./DashboardPage";
import { AuthProvider } from "../auth/AuthProvider";
import * as authModule from "../auth/auth";
import { demoReport, demoRun, demoSnapshot } from "./mockData";

vi.mock("../auth/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../auth/auth")>();
  return {
    ...actual,
    isCognitoConfigured: vi.fn(() => false),
    getCurrentUser: vi.fn(async () => null),
    getStoredAccessToken: vi.fn(async () => null),
  };
});

const API_BASE = "https://api.coldchain.example.com";

const demoSummaries = [
  {
    run_id: "00000000-0000-0000-0000-000000001042",
    status: "needs_review",
    stage: "ready",
    created_at: "2026-09-17T12:00:00Z",
    completed_at: "2026-09-17T12:04:30Z",
    report_id: "00000000-0000-0000-0000-000000002042",
    review: null,
    generation_mode: "deterministic_only",
    is_public_demo: true,
    label: "Public door exposure demo",
  },
  {
    run_id: "00000000-0000-0000-0000-000000001043",
    status: "needs_review",
    stage: "ready",
    created_at: "2026-09-17T12:00:00Z",
    completed_at: "2026-09-17T12:04:30Z",
    report_id: "00000000-0000-0000-0000-000000002043",
    review: null,
    generation_mode: "deterministic_only",
    is_public_demo: true,
    label: "Public unresolved excursion demo",
  },
];

import { navigation } from "./navigation";

describe("DashboardPage API-backed interactions", () => {
  let originalFetch: typeof fetch;
  let assignSpy: any;

  beforeEach(() => {
    vi.stubEnv("VITE_API_BASE_URL", API_BASE);
    originalFetch = globalThis.fetch;
    assignSpy = vi.spyOn(navigation, "assign").mockImplementation(() => {});
    window.history.pushState({}, "", "/");
    vi.mocked(authModule.isCognitoConfigured).mockReturnValue(false);
    vi.mocked(authModule.getCurrentUser).mockResolvedValue(null);
    vi.mocked(authModule.getStoredAccessToken).mockResolvedValue(null);
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    globalThis.fetch = originalFetch;
    assignSpy.mockRestore();
    vi.clearAllMocks();
  });

  it("loads and displays live public demo investigation without fixture banner", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/demo-runs`) {
        return new Response(JSON.stringify(demoSummaries), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042`) {
        return new Response(JSON.stringify({ ...demoRun, is_public_demo: true, label: "Public door exposure demo" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/report`) {
        return new Response(JSON.stringify(demoReport), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`Unexpected request to ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    render(
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>,
    );

    // Should not show demo fixture mode banner
    await waitFor(() => {
      expect(screen.queryByText(/Demo fixture mode/)).not.toBeInTheDocument();
    });

    // Should display live heading and live demo selector options
    expect(await screen.findByText("Temperature exceeded the configured limit")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Demo case" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Public door exposure demo" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Public unresolved excursion demo" })).toBeInTheDocument();

    // Summary metric cards
    const summary = screen.getByRole("region", { name: "Investigation summary" });
    expect(summary).toHaveTextContent("Observed peak");
    expect(summary).toHaveTextContent("9.4°C");
  });

  it("navigates with demo_run_id when operator switches demo case in public demo mode", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/demo-runs`) {
        return new Response(JSON.stringify(demoSummaries), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042`) {
        return new Response(JSON.stringify({ ...demoRun, is_public_demo: true }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/report`) {
        return new Response(JSON.stringify(demoReport), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`Unexpected request to ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    render(
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>,
    );

    const select = await screen.findByRole("combobox", { name: "Demo case" });
    fireEvent.change(select, { target: { value: "00000000-0000-0000-0000-000000001043" } });

    expect(assignSpy).toHaveBeenCalledWith(
      expect.stringContaining("demo_run_id=00000000-0000-0000-0000-000000001043"),
    );
  });

  it("handles public demo report download via public download endpoint", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/demo-runs`) {
        return new Response(JSON.stringify(demoSummaries), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042`) {
        return new Response(JSON.stringify({ ...demoRun, is_public_demo: true }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/report`) {
        return new Response(JSON.stringify(demoReport), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/download`) {
        return new Response(JSON.stringify({ url: "https://s3.amazonaws.com/coldchain-demo-bucket/demo-report.json" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected request to ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    render(
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>,
    );

    const downloadButton = await screen.findByRole("button", { name: "Download" });
    expect(downloadButton).not.toBeDisabled();

    fireEvent.click(downloadButton);

    await waitFor(() => {
      expect(assignSpy).toHaveBeenCalledWith("https://s3.amazonaws.com/coldchain-demo-bucket/demo-report.json");
    });
  });

  it("allows manual retry of in-progress run and transitions to completed dashboard once ready", async () => {
    window.history.pushState({}, "", "/?run_id=00000000-0000-0000-0000-000000001099");

    vi.mocked(authModule.isCognitoConfigured).mockReturnValue(true);
    vi.mocked(authModule.getCurrentUser).mockResolvedValue({
      access_token: "operator-jwt-token-xyz",
      expired: false,
    } as any);
    vi.mocked(authModule.getStoredAccessToken).mockResolvedValue("operator-jwt-token-xyz");

    let runPollCount = 0;
    const inProgressRun = {
      run_id: "00000000-0000-0000-0000-000000001099",
      shipment_id: "00000000-0000-0000-0000-000000003048",
      snapshot_id: "00000000-0000-0000-0000-000000004048",
      status: "running" as const,
      stage: "detecting" as const,
      created_at: "2026-09-17T12:00:00Z",
      completed_at: null,
      report_id: null,
      review: null,
      generation_mode: null,
      stage_events: [],
      report_summary: null,
      error: null,
    };

    const completedRun = {
      ...inProgressRun,
      status: "completed" as const,
      stage: "ready" as const,
      completed_at: "2026-09-17T12:04:30Z",
      report_id: "00000000-0000-0000-0000-000000002042",
      generation_mode: "deterministic_only" as const,
      report_summary: { outcome: "hypothesis_supported" as const, primary_hypothesis: "door_exposure" as const, review_required: true },
    };

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/runs/00000000-0000-0000-0000-000000001099`) {
        runPollCount += 1;
        if (runPollCount === 1) {
          return new Response(JSON.stringify(inProgressRun), { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return new Response(JSON.stringify(completedRun), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/runs/00000000-0000-0000-0000-000000001099/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/runs/00000000-0000-0000-0000-000000001099/report`) {
        if (runPollCount === 1) {
          return new Response(
            JSON.stringify({
              error: { code: "REPORT_NOT_READY", message: "Report not ready", request_id: "req-not-ready", retryable: false },
            }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify(demoReport), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`Unexpected request to ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    render(
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>,
    );

    // Initial state: in progress with API run data
    expect(await screen.findByText(/Run 00000000-0000-0000-0000-000000001099 is at the/)).toBeInTheDocument();
    expect(screen.getByText("Investigation in progress")).toBeInTheDocument();
    expect(screen.getByText("Detecting")).toBeInTheDocument();

    // Trigger retry/next poll to simulate transition to completed
    const retryBtn = screen.getByRole("button", { name: "Retry" });
    fireEvent.click(retryBtn);

    // Transition to completed dashboard view
    expect(await screen.findByText("Temperature exceeded the configured limit")).toBeInTheDocument();
    expect(screen.queryByText("Investigation in progress")).not.toBeInTheDocument();
  });

  it("submits operator review with JWT authorization header and displays saved review identity", async () => {
    window.history.pushState({}, "", `/?run_id=${demoRun.run_id}`);

    vi.mocked(authModule.isCognitoConfigured).mockReturnValue(true);
    vi.mocked(authModule.getCurrentUser).mockResolvedValue({
      access_token: "operator-valid-jwt",
      expired: false,
    } as any);
    vi.mocked(authModule.getStoredAccessToken).mockResolvedValue("operator-valid-jwt");

    let submittedHeaders: Headers | undefined;
    let submittedBody: any;

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/runs/${demoRun.run_id}`) {
        return new Response(JSON.stringify(demoRun), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/runs/${demoRun.run_id}/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/runs/${demoRun.run_id}/report`) {
        return new Response(JSON.stringify(demoReport), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/runs/${demoRun.run_id}/review` && init?.method === "POST") {
        submittedHeaders = new Headers(init.headers);
        submittedBody = JSON.parse(String(init.body));
        const reviewResponse = {
          review_id: "00000000-0000-0000-0000-000000009555",
          run_id: demoRun.run_id,
          report_id: demoReport.report_id,
          decision: "acknowledged",
          note: submittedBody.note,
          actor_sub: "operator-auditor-sub-99",
          reviewed_at: "2026-09-17T12:35:00Z",
        };
        return new Response(JSON.stringify(reviewResponse), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`Unexpected request to ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    render(
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>,
    );

    // Wait until fixture mode and in-progress loading clear and review button is present
    await waitFor(() => {
      expect(screen.queryByText(/Demo fixture mode/)).not.toBeInTheDocument();
      expect(screen.queryByText("Investigation in progress")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Acknowledge review" })).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText("Add review note…");
    fireEvent.change(textarea, { target: { value: "Checked door sensor logs and confirmed excursion." } });

    const ackButton = screen.getByRole("button", { name: "Acknowledge review" });
    expect(ackButton).not.toBeDisabled();

    fireEvent.click(ackButton);

    expect(await screen.findByText("Review saved.")).toBeInTheDocument();
    expect(await screen.findByTestId("reviewer-identity")).toHaveTextContent("operator-auditor-sub-99");
    expect(screen.getByText(/Recorded Review · acknowledged/)).toBeInTheDocument();

    // Verify auth header and payload sent to POST /v1/runs/:id/review
    expect(submittedHeaders?.get("Authorization")).toBe("Bearer operator-valid-jwt");
    expect(submittedBody).toEqual({
      decision: "acknowledged",
      note: "Checked door sensor logs and confirmed excursion.",
      report_id: "00000000-0000-0000-0000-000000002042",
    });
  });

  it("submits new run with Idempotency-Key header when operator clicks Run investigation", async () => {
    vi.mocked(authModule.isCognitoConfigured).mockReturnValue(true);
    vi.mocked(authModule.getCurrentUser).mockResolvedValue({
      access_token: "operator-jwt-create",
      expired: false,
    } as any);
    vi.mocked(authModule.getStoredAccessToken).mockResolvedValue("operator-jwt-create");

    let postHeaders: Headers | undefined;
    let postBody: any;

    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/demo-runs`) {
        return new Response(JSON.stringify(demoSummaries), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042`) {
        return new Response(JSON.stringify({ ...demoRun, is_public_demo: true }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/report`) {
        return new Response(JSON.stringify(demoReport), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/scenarios`) {
        return new Response(
          JSON.stringify([{ scenario_id: "00000000-0000-0000-0000-000000000001", label: "Door sensor anomaly" }]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === `${API_BASE}/v1/runs` && init?.method === "POST") {
        postHeaders = new Headers(init.headers);
        postBody = JSON.parse(String(init.body));
        return new Response(
          JSON.stringify({
            run_id: "00000000-0000-0000-0000-000000008888",
            status: "pending_enqueue",
            poll_url: "/v1/runs/00000000-0000-0000-0000-000000008888",
          }),
          { status: 202, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected request to ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    render(
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>,
    );

    const runButton = await screen.findByRole("button", { name: "Run investigation" });
    fireEvent.click(runButton);

    await waitFor(() => {
      expect(assignSpy).toHaveBeenCalledWith(
        expect.stringContaining("run_id=00000000-0000-0000-0000-000000008888"),
      );
    });

    // Validate Idempotency-Key header is a valid UUID
    const idempotencyKey = postHeaders?.get("Idempotency-Key");
    expect(idempotencyKey).toMatch(/^[0-9a-fA-F-]{36}$/);
    expect(postBody.scenario_id).toBe("00000000-0000-0000-0000-000000000001");
    expect(typeof postBody.seed).toBe("number");
  });

  it("displays error alert when API fails and allows retry recovery", async () => {
    let shouldFail = true;

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/demo-runs`) {
        if (shouldFail) {
          return new Response(
            JSON.stringify({
              error: {
                code: "SERVICE_UNAVAILABLE",
                message: "Backend simulation service unavailable",
                request_id: "req-fail-01",
                retryable: true,
              },
            }),
            { status: 503, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify(demoSummaries), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042`) {
        return new Response(JSON.stringify({ ...demoRun, is_public_demo: true }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url === `${API_BASE}/v1/demo-runs/00000000-0000-0000-0000-000000001042/report`) {
        return new Response(JSON.stringify(demoReport), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      throw new Error(`Unexpected request to ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    render(
      <AuthProvider>
        <DashboardPage />
      </AuthProvider>,
    );

    // Initial failure displayed
    expect(await screen.findByRole("alert")).toHaveTextContent("Backend simulation service unavailable");

    // Fix backend and retry
    shouldFail = false;
    const retryButton = screen.getByRole("button", { name: "Retry" });
    fireEvent.click(retryButton);

    // Recovers successfully
    expect(await screen.findByText("Temperature exceeded the configured limit")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
