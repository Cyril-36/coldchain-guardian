import { useEffect, useState } from "react";
import { ApiClient } from "../api/client";
import { ApiClientError } from "../api/errors";
import { createApiEndpoints } from "../api/endpoints";
import type { Report, Run, Snapshot } from "../types/contracts";
import { useAuth } from "../auth/AuthProvider";
import { demoReport, demoRun, demoSnapshot } from "./mockData";

interface RunData {
  run: Run; snapshot: Snapshot; report: Report; source: "api" | "fixture"; protectedRun: boolean;
  snapshotReady: boolean; reportReady: boolean; loading: boolean; error: Error | null; retry: () => void;
}
const ACTIVE_POLL_MS = 2_000;
const BACKOFF_POLL_MS = 5_000;
const BACKOFF_AFTER_MS = 30_000;
function getRunIdFromUrl() { return new URLSearchParams(window.location.search).get("run_id"); }
function isTerminal(status: Run["status"]) { return status === "completed" || status === "needs_review" || status === "failed"; }
function shouldRetry(error: unknown) { return error instanceof ApiClientError ? error.retryable : true; }

export function useRunData(): RunData {
  const auth = useAuth();
  const [retryNonce, setRetryNonce] = useState(0);
  const retry = () => setRetryNonce((value) => value + 1);
  const [data, setData] = useState<RunData>({ run: demoRun, snapshot: demoSnapshot, report: demoReport, source: "fixture", protectedRun: false, snapshotReady: true, reportReady: true, loading: false, error: null, retry });

  useEffect(() => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL;
    const runId = getRunIdFromUrl();
    if (!baseUrl || !runId) {
      setData((current) => ({ ...current, protectedRun: false, source: "fixture", loading: false, error: null, retry }));
      return;
    }
    if (auth.loading) { setData((current) => ({ ...current, protectedRun: true, source: "api", loading: true, error: null, retry })); return; }
    if (!auth.authenticated) { setData((current) => ({ ...current, protectedRun: true, source: "api", loading: false, snapshotReady: false, reportReady: false, error: null, retry })); return; }

    let cancelled = false; let pollTimer: number | undefined; const startedAt = Date.now();
    const client = new ApiClient({ baseUrl, tokenProvider: auth.getAccessToken }); const api = createApiEndpoints(client);
    const clearPollTimer = () => { if (pollTimer !== undefined) { window.clearTimeout(pollTimer); pollTimer = undefined; } };
    const hydrateArtifacts = async (currentRun: Run) => {
      const [snapshotResult, reportResult] = await Promise.allSettled([api.getSnapshot(runId), api.getReport(runId)]); if (cancelled) return;
      const snapshotReady = snapshotResult.status === "fulfilled"; const reportReady = reportResult.status === "fulfilled";
      const artifactError = isTerminal(currentRun.status) && (!snapshotReady || !reportReady) ? new Error("Investigation artifacts are unavailable") : null;
      setData((current) => ({ ...current, run: currentRun, snapshot: snapshotReady ? snapshotResult.value : current.snapshot, report: reportReady ? reportResult.value : current.report, source: "api", protectedRun: true, snapshotReady: snapshotReady || current.snapshotReady, reportReady: reportReady || current.reportReady, loading: false, error: artifactError, retry }));
    };
    const poll = async () => {
      if (cancelled || document.visibilityState === "hidden") return;
      try {
        const run = await api.getRun(runId); if (cancelled) return;
        setData((current) => ({ ...current, run, source: "api", protectedRun: true, loading: false, error: null, retry }));
        if (isTerminal(run.status)) { clearPollTimer(); await hydrateArtifacts(run); return; }
        const delay = Date.now() - startedAt >= BACKOFF_AFTER_MS ? BACKOFF_POLL_MS : ACTIVE_POLL_MS; pollTimer = window.setTimeout(() => void poll(), delay);
      } catch (error: unknown) {
        if (cancelled) return;
        setData((current) => ({ ...current, protectedRun: true, loading: false, error: error instanceof Error ? error : new Error("Unable to refresh investigation data"), retry }));
        if (shouldRetry(error)) { const delay = Date.now() - startedAt >= BACKOFF_AFTER_MS ? BACKOFF_POLL_MS : ACTIVE_POLL_MS; pollTimer = window.setTimeout(() => void poll(), delay); } else clearPollTimer();
      }
    };
    const handleVisibilityChange = () => { clearPollTimer(); if (document.visibilityState === "visible") void poll(); };
    const initialise = async () => {
      setData((current) => ({ ...current, protectedRun: true, source: "api", snapshotReady: false, reportReady: false, loading: true, error: null, retry }));
      try {
        const run = await api.getRun(runId); if (cancelled) return;
        setData((current) => ({ ...current, run, source: "api", protectedRun: true, loading: true, error: null, retry }));
        await hydrateArtifacts(run); if (cancelled || isTerminal(run.status)) return;
        pollTimer = window.setTimeout(() => void poll(), ACTIVE_POLL_MS);
      } catch (error: unknown) { if (!cancelled) setData((current) => ({ ...current, source: "api", protectedRun: true, snapshotReady: false, reportReady: false, loading: false, error: error instanceof Error ? error : new Error("Unable to load investigation data"), retry })); }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange); void initialise();
    return () => { cancelled = true; clearPollTimer(); document.removeEventListener("visibilitychange", handleVisibilityChange); };
  }, [auth.authenticated, auth.getAccessToken, auth.loading, retryNonce]);
  return data;
}
