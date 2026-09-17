import { useEffect, useState } from "react";
import { ApiClient } from "../api/client";
import { createApiEndpoints } from "../api/endpoints";
import type { Report, Run, Snapshot } from "../types/contracts";
import { demoReport, demoRun, demoSnapshot } from "./mockData";

interface RunData {
  run: Run;
  snapshot: Snapshot;
  report: Report;
  source: "api" | "fixture";
  loading: boolean;
  error: Error | null;
}

function getRunIdFromUrl() {
  return new URLSearchParams(window.location.search).get("run_id");
}

export function useRunData(): RunData {
  const [data, setData] = useState<RunData>({ run: demoRun, snapshot: demoSnapshot, report: demoReport, source: "fixture", loading: false, error: null });

  useEffect(() => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL;
    const runId = getRunIdFromUrl();
    if (!baseUrl || !runId) return;

    let cancelled = false;
    const client = new ApiClient({ baseUrl });
    const api = createApiEndpoints(client);
    setData((current) => ({ ...current, loading: true, error: null }));

    Promise.all([api.getRun(runId), api.getSnapshot(runId), api.getReport(runId)])
      .then(([run, snapshot, report]) => {
        if (!cancelled) setData({ run, snapshot, report, source: "api", loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (!cancelled) setData((current) => ({ ...current, loading: false, error: error instanceof Error ? error : new Error("Unable to load investigation data") }));
      });

    return () => { cancelled = true; };
  }, []);

  return data;
}
