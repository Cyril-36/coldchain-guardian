import { useEffect, useState } from "react";
import { ApiClient } from "../api/client";
import { ApiClientError } from "../api/errors";
import { createApiEndpoints } from "../api/endpoints";
import type { Report, Run, Snapshot } from "../types/contracts";
import { demoReport, demoRun, demoSnapshot } from "./mockData";

interface RunData {
  run: Run;
  snapshot: Snapshot;
  report: Report;
  source: "api" | "fixture";
  snapshotReady: boolean;
  reportReady: boolean;
  loading: boolean;
  error: Error | null;
}

const ACTIVE_POLL_MS = 2_000;
const BACKOFF_POLL_MS = 5_000;
const BACKOFF_AFTER_MS = 30_000;

function getRunIdFromUrl() {
  return new URLSearchParams(window.location.search).get("run_id");
}

function isTerminal(status: Run["status"]) {
  return status === "completed" || status === "needs_review" || status === "failed";
}

function shouldRetry(error: unknown) {
  return error instanceof ApiClientError ? error.retryable : true;
}

export function useRunData(): RunData {
  const [data, setData] = useState<RunData>({
    run: demoRun,
    snapshot: demoSnapshot,
    report: demoReport,
    source: "fixture",
    snapshotReady: true,
    reportReady: true,
    loading: false,
    error: null,
  });

  useEffect(() => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL;
    const runId = getRunIdFromUrl();
    if (!baseUrl || !runId) return;

    let cancelled = false;
    let pollTimer: number | undefined;
    const startedAt = Date.now();
    const client = new ApiClient({ baseUrl });
    const api = createApiEndpoints(client);

    const clearPollTimer = () => {
      if (pollTimer !== undefined) {
        window.clearTimeout(pollTimer);
        pollTimer = undefined;
      }
    };

    const hydrateArtifacts = async (currentRun: Run) => {
      const [snapshotResult, reportResult] = await Promise.allSettled([
        api.getSnapshot(runId),
        api.getReport(runId),
      ]);

      if (cancelled) return;

      setData((current) => ({
        ...current,
        run: currentRun,
        snapshot: snapshotResult.status === "fulfilled" ? snapshotResult.value : current.snapshot,
        report: reportResult.status === "fulfilled" ? reportResult.value : current.report,
        source: "api",
        snapshotReady: snapshotResult.status === "fulfilled" || current.snapshotReady,
        reportReady: reportResult.status === "fulfilled" || current.reportReady,
        loading: false,
        error: null,
      }));
    };

    const poll = async () => {
      if (cancelled || document.visibilityState === "hidden") return;

      try {
        const run = await api.getRun(runId);
        if (cancelled) return;

        setData((current) => ({
          ...current,
          run,
          source: "api",
          loading: false,
          error: null,
        }));

        if (isTerminal(run.status)) {
          clearPollTimer();
          await hydrateArtifacts(run);
          return;
        }

        const elapsed = Date.now() - startedAt;
        const delay = elapsed >= BACKOFF_AFTER_MS ? BACKOFF_POLL_MS : ACTIVE_POLL_MS;
        pollTimer = window.setTimeout(() => void poll(), delay);
      } catch (error: unknown) {
        if (cancelled) return;

        setData((current) => ({
          ...current,
          loading: false,
          error: error instanceof Error ? error : new Error("Unable to refresh investigation data"),
        }));

        if (shouldRetry(error)) {
          const elapsed = Date.now() - startedAt;
          const delay = elapsed >= BACKOFF_AFTER_MS ? BACKOFF_POLL_MS : ACTIVE_POLL_MS;
          pollTimer = window.setTimeout(() => void poll(), delay);
        } else {
          clearPollTimer();
        }
      }
    };

    const handleVisibilityChange = () => {
      clearPollTimer();
      if (document.visibilityState === "visible") {
        void poll();
      }
    };

    const initialise = async () => {
      setData((current) => ({
        ...current,
        source: "api",
        snapshotReady: false,
        reportReady: false,
        loading: true,
        error: null,
      }));

      try {
        const run = await api.getRun(runId);
        if (cancelled) return;

        setData((current) => ({ ...current, run, source: "api", loading: true, error: null }));

        await hydrateArtifacts(run);
        if (cancelled) return;

        if (isTerminal(run.status)) return;
        pollTimer = window.setTimeout(() => void poll(), ACTIVE_POLL_MS);
      } catch (error: unknown) {
        if (!cancelled) {
          setData((current) => ({
            ...current,
            source: "api",
            snapshotReady: false,
            reportReady: false,
            loading: false,
            error: error instanceof Error ? error : new Error("Unable to load investigation data"),
          }));
        }
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    void initialise();

    return () => {
      cancelled = true;
      clearPollTimer();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  return data;
}
