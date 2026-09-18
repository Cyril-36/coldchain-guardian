import { useEffect, useState } from "react";
import { ApiClient } from "../api/client";
import { ApiClientError } from "../api/errors";
import { createApiEndpoints } from "../api/endpoints";
import type { DemoRunSummary, Report, Run, Snapshot } from "../types/contracts";
import { useAuth } from "../auth/AuthProvider";
import { demoReport, demoRun, demoSnapshot, mockCases } from "./mockData";

export interface RunData {
  run: Run;
  snapshot: Snapshot;
  report: Report;
  source: "api" | "fixture";
  protectedRun: boolean;
  snapshotReady: boolean;
  reportReady: boolean;
  loading: boolean;
  error: Error | null;
  retry: () => void;
  demoRuns: DemoRunSummary[];
  selectedDemoId: string | null;
}
const ACTIVE_POLL_MS = 2_000;
const BACKOFF_POLL_MS = 5_000;
const BACKOFF_AFTER_MS = 30_000;
function getRunIdFromUrl() { return new URLSearchParams(window.location.search).get("run_id"); }
function getDemoRunIdFromUrl() { return new URLSearchParams(window.location.search).get("demo_run_id"); }
function getCaseIdFromUrl() { return new URLSearchParams(window.location.search).get("case"); }
function isTerminal(status: Run["status"]) { return status === "completed" || status === "needs_review" || status === "failed"; }
function shouldRetry(error: unknown) { return error instanceof ApiClientError ? error.retryable : true; }

const fixtureDemoSummaries: DemoRunSummary[] = Object.values(mockCases).map((c) => ({
  ...c.run,
  is_public_demo: true as const,
  label: c.label,
}));

export function useRunData(): RunData {
  const auth = useAuth();
  const [retryNonce, setRetryNonce] = useState(0);
  const retry = () => setRetryNonce((value) => value + 1);

  const initialRunId = getRunIdFromUrl();
  const initialCaseId = getCaseIdFromUrl() ?? "door";
  const initialFixture = mockCases[initialCaseId] ?? mockCases.door;

  const [data, setData] = useState<RunData>({
    run: initialRunId ? { ...initialFixture.run, run_id: initialRunId } : initialFixture.run,
    snapshot: initialFixture.snapshot,
    report: initialFixture.report,
    source: initialRunId ? "api" : "fixture",
    protectedRun: Boolean(initialRunId),
    snapshotReady: !initialRunId,
    reportReady: !initialRunId,
    loading: Boolean(initialRunId),
    error: null,
    retry,
    demoRuns: fixtureDemoSummaries,
    selectedDemoId: initialFixture.id,
  });

  useEffect(() => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL;
    const runId = getRunIdFromUrl();
    const demoRunId = getDemoRunIdFromUrl();
    const caseId = getCaseIdFromUrl();

    if (!baseUrl) {
      if (import.meta.env.PROD) {
        setData((current) => ({ ...current, protectedRun: false, source: "api", loading: false, snapshotReady: false, reportReady: false, error: new Error("API base URL is not configured"), retry }));
      } else if (!runId && !demoRunId) {
        const fixtureCase = mockCases[caseId ?? "door"] ?? mockCases.door;
        setData((current) => ({
          ...current,
          run: fixtureCase.run,
          snapshot: fixtureCase.snapshot,
          report: fixtureCase.report,
          protectedRun: false,
          source: "fixture",
          loading: false,
          error: null,
          demoRuns: fixtureDemoSummaries,
          selectedDemoId: fixtureCase.id,
          retry,
        }));
      }
      return;
    }

    if (runId) {
      if (auth.loading) { setData((current) => ({ ...current, protectedRun: true, source: "api", loading: true, snapshotReady: false, reportReady: false, error: null, retry })); return; }
      if (!auth.authenticated) { setData((current) => ({ ...current, protectedRun: true, source: "api", loading: false, snapshotReady: false, reportReady: false, error: null, retry })); return; }
    }

    let cancelled = false; let pollTimer: number | undefined; const startedAt = Date.now();
    const client = new ApiClient({ baseUrl, tokenProvider: runId ? auth.getAccessToken : undefined }); const api = createApiEndpoints(client);
    const clearPollTimer = () => { if (pollTimer !== undefined) { window.clearTimeout(pollTimer); pollTimer = undefined; } };
    const hydrateArtifacts = async (currentRun: Run, protectedRequest: boolean) => {
      const [snapshotResult, reportResult] = await Promise.allSettled([
        protectedRequest ? api.getSnapshot(currentRun.run_id) : api.getDemoSnapshot(currentRun.run_id),
        protectedRequest ? api.getReport(currentRun.run_id) : api.getDemoReport(currentRun.run_id),
      ]);
      if (cancelled) return;
      const snapshotReady = snapshotResult.status === "fulfilled"; const reportReady = reportResult.status === "fulfilled";
      const artifactError = isTerminal(currentRun.status) && (!snapshotReady || !reportReady) ? new Error("Investigation artifacts are unavailable") : null;
      setData((current) => ({ ...current, run: currentRun, snapshot: snapshotReady ? snapshotResult.value : current.snapshot, report: reportReady ? reportResult.value : current.report, source: "api", protectedRun: protectedRequest, snapshotReady, reportReady, loading: false, error: artifactError, retry }));
    };
    const poll = async () => {
      if (cancelled || document.visibilityState === "hidden") return;
      try {
        const run = await api.getRun(runId!); if (cancelled) return;
        setData((current) => ({ ...current, run, source: "api", protectedRun: true, loading: false, error: null, retry }));
        if (isTerminal(run.status)) { clearPollTimer(); await hydrateArtifacts(run, true); return; }
        const delay = Date.now() - startedAt >= BACKOFF_AFTER_MS ? BACKOFF_POLL_MS : ACTIVE_POLL_MS; pollTimer = window.setTimeout(() => void poll(), delay);
      } catch (error: unknown) {
        if (cancelled) return;
        setData((current) => ({ ...current, protectedRun: true, loading: false, error: error instanceof Error ? error : new Error("Unable to refresh investigation data"), retry }));
        if (shouldRetry(error)) { const delay = Date.now() - startedAt >= BACKOFF_AFTER_MS ? BACKOFF_POLL_MS : ACTIVE_POLL_MS; pollTimer = window.setTimeout(() => void poll(), delay); } else clearPollTimer();
      }
    };
    const handleVisibilityChange = () => { clearPollTimer(); if (document.visibilityState === "visible" && runId) void poll(); };
    const initialiseProtected = async () => {
      setData((current) => ({ ...current, protectedRun: true, source: "api", snapshotReady: false, reportReady: false, loading: true, error: null, retry }));
      try {
        const run = await api.getRun(runId!); if (cancelled) return;
        setData((current) => ({ ...current, run, source: "api", protectedRun: true, loading: true, error: null, retry }));
        await hydrateArtifacts(run, true); if (cancelled || isTerminal(run.status)) return;
        pollTimer = window.setTimeout(() => void poll(), ACTIVE_POLL_MS);
      } catch (error: unknown) { if (!cancelled) setData((current) => ({ ...current, source: "api", protectedRun: true, snapshotReady: false, reportReady: false, loading: false, error: error instanceof Error ? error : new Error("Unable to load investigation data"), retry })); }
    };
    const initialisePublicDemo = async () => {
      setData((current) => ({ ...current, protectedRun: false, source: "api", snapshotReady: false, reportReady: false, loading: true, error: null, retry }));
      try {
        const demos = await api.listDemoRuns();
        const selected = demoRunId ? demos.find((item) => item.run_id === demoRunId) : demos[0];
        if (!selected) throw new Error("No public demo runs are available");
        const run = await api.getDemoRun(selected.run_id); if (cancelled) return;
        setData((current) => ({ ...current, demoRuns: demos, selectedDemoId: selected.run_id }));
        await hydrateArtifacts(run, false);
      } catch (error: unknown) {
        if (!cancelled) setData((current) => ({ ...current, source: "api", protectedRun: false, snapshotReady: false, reportReady: false, loading: false, error: error instanceof Error ? error : new Error("Unable to load public demo") , retry }));
      }
    };

    if (runId) {
      document.addEventListener("visibilitychange", handleVisibilityChange);
      void initialiseProtected();
    } else {
      void initialisePublicDemo();
    }
    return () => { cancelled = true; clearPollTimer(); document.removeEventListener("visibilitychange", handleVisibilityChange); };
  }, [auth.authenticated, auth.getAccessToken, auth.loading, retryNonce]);
  return data;
}
