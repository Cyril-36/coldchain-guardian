import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRunData } from "./useRunData";
import { AuthProvider } from "../auth/AuthProvider";
import * as authModule from "../auth/auth";
import { demoReport, demoSnapshot } from "./mockData";
import type { Run } from "../types/contracts";

vi.mock("../auth/auth", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../auth/auth")>();
  return {
    ...actual,
    isCognitoConfigured: vi.fn(() => true),
    getCurrentUser: vi.fn(async () => ({ access_token: "operator-valid-jwt", expired: false } as any)),
    getStoredAccessToken: vi.fn(async () => "operator-valid-jwt"),
  };
});

const API_BASE = "https://api.coldchain.example.com";
const RUN_ID = "00000000-0000-0000-0000-000000001099";

const inProgressRun: Run = {
  run_id: RUN_ID,
  shipment_id: "00000000-0000-0000-0000-000000003048",
  snapshot_id: "00000000-0000-0000-0000-000000004048",
  status: "running",
  stage: "detecting",
  created_at: "2026-09-17T12:00:00Z",
  completed_at: null,
  report_id: null,
  review: null,
  generation_mode: null,
  stage_events: [],
  report_summary: null,
  error: null,
};

const completedRun: Run = {
  ...inProgressRun,
  status: "completed",
  stage: "ready",
  completed_at: "2026-09-17T12:04:30Z",
  report_id: "00000000-0000-0000-0000-000000002042",
  generation_mode: "deterministic_only",
  report_summary: {
    outcome: "hypothesis_supported",
    primary_hypothesis: "door_exposure",
    review_required: true,
  },
};

const wrapper = ({ children }: { children: React.ReactNode }) => <AuthProvider>{children}</AuthProvider>;

describe("useRunData polling, backoff, and tab visibility behaviors", () => {
  let originalFetch: typeof fetch;
  let originalVisibilityState: PropertyDescriptor | undefined;

  beforeEach(() => {
    vi.stubEnv("VITE_API_BASE_URL", API_BASE);
    window.history.pushState({}, "", `/?run_id=${RUN_ID}`);
    originalFetch = globalThis.fetch;
    originalVisibilityState = Object.getOwnPropertyDescriptor(document, "visibilityState");
    vi.mocked(authModule.isCognitoConfigured).mockReturnValue(true);
    vi.mocked(authModule.getCurrentUser).mockResolvedValue({ access_token: "operator-valid-jwt", expired: false } as any);
    vi.mocked(authModule.getStoredAccessToken).mockResolvedValue("operator-valid-jwt");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    globalThis.fetch = originalFetch;
    if (originalVisibilityState) {
      Object.defineProperty(document, "visibilityState", originalVisibilityState);
    }
    vi.restoreAllMocks();
  });

  it("schedules polling at 2-second intervals while run is in-progress", async () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/runs/${RUN_ID}`) {
        return new Response(JSON.stringify(inProgressRun), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/report`) {
        return new Response(
          JSON.stringify({
            error: { code: "REPORT_NOT_READY", message: "Not ready", request_id: "req-1", retryable: false },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected url: ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    const { result, unmount } = renderHook(() => useRunData(), { wrapper });

    await waitFor(() => {
      expect(result.current.run.status).toBe("running");
      expect(result.current.reportReady).toBe(false);
      expect(result.current.report).toBeNull();
      // Verify initial active polling delay is exactly 2,000ms
      expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 2000);
    });

    unmount();
  });

  it("backs off polling delay to 5 seconds after 30 seconds of elapsed runtime", async () => {
    const realSetTimeout = window.setTimeout;
    let capturedPollCallback: (() => void) | undefined;
    const setTimeoutSpy = vi.spyOn(window, "setTimeout").mockImplementation((cb: any, delay: any, ...args: any[]) => {
      if (typeof cb === "function" && (delay === 2000 || delay === 5000)) {
        capturedPollCallback = cb;
      }
      return realSetTimeout(cb, delay, ...args);
    });

    let timeOffset = 0;
    const realDateNow = Date.now.bind(Date);
    vi.spyOn(Date, "now").mockImplementation(() => realDateNow() + timeOffset);

    let getRunCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/runs/${RUN_ID}`) {
        getRunCount += 1;
        return new Response(JSON.stringify(inProgressRun), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/report`) {
        return new Response(
          JSON.stringify({
            error: { code: "REPORT_NOT_READY", message: "Not ready", request_id: "req-1", retryable: false },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected url: ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    const { unmount } = renderHook(() => useRunData(), { wrapper });

    await waitFor(() => {
      expect(getRunCount).toBe(1);
      expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 2000);
    });

    // Advance mock time past the 30-second backoff threshold (31 seconds elapsed)
    timeOffset = 31_000;

    // Trigger the scheduled poll callback directly
    expect(capturedPollCallback).toBeDefined();
    await act(async () => {
      capturedPollCallback!();
    });

    await waitFor(() => {
      expect(getRunCount).toBe(2);
      // The subsequent poll must be scheduled with BACKOFF_POLL_MS (5,000ms)
      expect(setTimeoutSpy).toHaveBeenCalledWith(expect.any(Function), 5000);
    });

    unmount();
  });

  it("suppresses polling when document visibilityState is hidden and triggers poll when visible", async () => {
    const realSetTimeout = window.setTimeout;
    let capturedPollCallback: (() => void) | undefined;
    vi.spyOn(window, "setTimeout").mockImplementation((cb: any, delay: any, ...args: any[]) => {
      if (typeof cb === "function" && (delay === 2000 || delay === 5000)) {
        capturedPollCallback = cb;
      }
      return realSetTimeout(cb, delay, ...args);
    });

    let getRunCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/runs/${RUN_ID}`) {
        getRunCount += 1;
        return new Response(JSON.stringify(inProgressRun), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/report`) {
        return new Response(
          JSON.stringify({
            error: { code: "REPORT_NOT_READY", message: "Not ready", request_id: "req-1", retryable: false },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected url: ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    const { unmount } = renderHook(() => useRunData(), { wrapper });

    await waitFor(() => {
      expect(getRunCount).toBe(1);
    });

    // Set visibilityState to 'hidden'
    Object.defineProperty(document, "visibilityState", { value: "hidden", writable: true, configurable: true });

    // When poll timer fires while tab is hidden, poll() returns immediately without fetching
    await act(async () => {
      capturedPollCallback!();
    });

    // Count must remain 1 (no new getRun request was made)
    expect(getRunCount).toBe(1);

    // Now restore visibilityState to 'visible' and fire visibilitychange event
    Object.defineProperty(document, "visibilityState", { value: "visible", writable: true, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    // Immediate poll is triggered upon tab becoming visible
    await waitFor(() => {
      expect(getRunCount).toBe(2);
    });

    unmount();
  });

  it("stops polling and hydrates report and snapshot once terminal status is reached", async () => {
    const realSetTimeout = window.setTimeout;
    let capturedPollCallback: (() => void) | undefined;
    const setTimeoutSpy = vi.spyOn(window, "setTimeout").mockImplementation((cb: any, delay: any, ...args: any[]) => {
      if (typeof cb === "function" && (delay === 2000 || delay === 5000)) {
        capturedPollCallback = cb;
      }
      return realSetTimeout(cb, delay, ...args);
    });
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");

    let getRunCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/runs/${RUN_ID}`) {
        getRunCount += 1;
        if (getRunCount === 1) {
          return new Response(JSON.stringify(inProgressRun), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify(completedRun), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/report`) {
        if (getRunCount === 1) {
          return new Response(
            JSON.stringify({
              error: { code: "REPORT_NOT_READY", message: "Not ready", request_id: "req-1", retryable: false },
            }),
            { status: 409, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(JSON.stringify(demoReport), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      throw new Error(`Unexpected url: ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    const { result, unmount } = renderHook(() => useRunData(), { wrapper });

    await waitFor(() => {
      expect(getRunCount).toBe(1);
      expect(result.current.run.status).toBe("running");
      expect(result.current.reportReady).toBe(false);
      expect(result.current.report).toBeNull();
    });

    // Reset setTimeoutSpy to count only polls scheduled after transition
    setTimeoutSpy.mockClear();

    // Trigger poll callback returning completedRun
    await act(async () => {
      capturedPollCallback!();
    });

    await waitFor(() => {
      expect(getRunCount).toBe(2);
      expect(result.current.run.status).toBe("completed");
      expect(result.current.reportReady).toBe(true);
      expect(result.current.snapshotReady).toBe(true);
      expect(result.current.report).toEqual(demoReport);
      // Verifies clearPollTimer was called
      expect(clearTimeoutSpy).toHaveBeenCalled();
      // Verifies NO new poll timer was scheduled after reaching terminal status
      expect(setTimeoutSpy).not.toHaveBeenCalledWith(expect.any(Function), 2000);
      expect(setTimeoutSpy).not.toHaveBeenCalledWith(expect.any(Function), 5000);
    });

    unmount();
  });

  it("never seeds report from fixture for API runs and keeps report strictly null while unready", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `${API_BASE}/v1/runs/${RUN_ID}`) {
        return new Response(JSON.stringify(inProgressRun), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/snapshot`) {
        return new Response(JSON.stringify(demoSnapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === `${API_BASE}/v1/runs/${RUN_ID}/report`) {
        return new Response(
          JSON.stringify({
            error: { code: "REPORT_NOT_READY", message: "Not ready", request_id: "req-1", retryable: false },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        );
      }
      throw new Error(`Unexpected url: ${url}`);
    });
    globalThis.fetch = fetchMock as any;

    const { result, unmount } = renderHook(() => useRunData(), { wrapper });

    // Initial state before fetch resolves must NOT leak fixture report
    expect(result.current.report).toBeNull();
    expect(result.current.reportReady).toBe(false);

    await waitFor(() => {
      expect(result.current.snapshotReady).toBe(true);
      expect(result.current.reportReady).toBe(false);
      expect(result.current.report).toBeNull();
    });

    unmount();
  });
});
