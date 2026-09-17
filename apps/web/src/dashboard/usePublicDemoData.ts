import { useEffect, useState } from "react";
import { ApiClient } from "../api/client";
import { createApiEndpoints } from "../api/endpoints";
import type { Report, Run, Snapshot } from "../types/contracts";

export interface PublicDemoData {
  run: Run | null;
  snapshot: Snapshot | null;
  report: Report | null;
  loading: boolean;
  error: Error | null;
  retry: () => void;
}

export function usePublicDemoData() {
  const [retryNonce, setRetryNonce] = useState(0);
  const retry = () => setRetryNonce((value) => value + 1);
  const [data, setData] = useState<PublicDemoData>({ run: null, snapshot: null, report: null, loading: true, error: null, retry });

  useEffect(() => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL;
    if (!baseUrl) {
      setData({ run: null, snapshot: null, report: null, loading: false, error: new Error("API base URL is not configured"), retry });
      return;
    }
    let cancelled = false;
    const api = createApiEndpoints(new ApiClient({ baseUrl }));
    setData((current) => ({ ...current, loading: true, error: null, retry }));
    void (async () => {
      try {
        const demos = await api.listDemoRuns();
        const selected = demos[0];
        if (!selected) throw new Error("No public demo investigations are available");
        const [run, snapshot, report] = await Promise.all([
          api.getDemoRun(selected.run_id),
          api.getDemoSnapshot(selected.run_id),
          api.getDemoReport(selected.run_id),
        ]);
        if (!cancelled) setData({ run, snapshot, report, loading: false, error: null, retry });
      } catch (cause) {
        if (!cancelled) setData((current) => ({ ...current, loading: false, error: cause instanceof Error ? cause : new Error("Unable to load public demo"), retry }));
      }
    })();
    return () => { cancelled = true; };
  }, [retryNonce]);

  return data;
}
