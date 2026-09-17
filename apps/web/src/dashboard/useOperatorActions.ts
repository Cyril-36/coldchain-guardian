import { useCallback, useRef, useState } from "react";
import { ApiClient } from "../api/client";
import { createApiEndpoints } from "../api/endpoints";
import type { CreateReviewRequest, Review } from "../types/contracts";
import { useAuth } from "../auth/AuthProvider";
import { createRunAttempt, type RunCreationAttempt } from "./runAttempt";

export { createRunAttempt } from "./runAttempt";
export type { RunCreationAttempt } from "./runAttempt";

export function useOperatorActions(runId: string | null) {
  const auth = useAuth();
  const [runCreating, setRunCreating] = useState(false);
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const runRequestRef = useRef<RunCreationAttempt | null>(null);
  const runCreatingRef = useRef(false);

  const getApi = useCallback(() => {
    if (!auth.authenticated) throw new Error("Operator session is required");
    const baseUrl = import.meta.env.VITE_API_BASE_URL;
    if (!baseUrl) throw new Error("API base URL is not configured");
    return createApiEndpoints(new ApiClient({ baseUrl, tokenProvider: auth.getAccessToken }));
  }, [auth.authenticated, auth.getAccessToken]);

  const createRun = useCallback(async () => {
    if (runCreatingRef.current) return;
    runCreatingRef.current = true;
    setRunCreating(true);
    setError(null);
    try {
      const api = getApi();
      const scenarios = await api.listScenarios();
      const scenario = scenarios[0];
      if (!scenario) throw new Error("No investigation scenarios are available");

      const request = createRunAttempt(runRequestRef.current, scenario.scenario_id);
      runRequestRef.current = request;

      const result = await api.createRun({ scenario_id: request.scenarioId, seed: request.seed }, request.idempotencyKey);
      runRequestRef.current = null;
      window.location.assign(`${window.location.pathname}?run_id=${encodeURIComponent(result.run_id)}`);
    } catch (cause) {
      const nextError = cause instanceof Error ? cause : new Error("Unable to start investigation");
      setError(nextError);
      throw nextError;
    } finally {
      runCreatingRef.current = false;
      setRunCreating(false);
    }
  }, [getApi]);

  const submitReview = useCallback(async (request: CreateReviewRequest) => {
    if (!runId) throw new Error("A protected run is required");
    setReviewSubmitting(true); setError(null);
    try { const result = await getApi().submitReview(runId, request); setReview(result); return result; }
    catch (cause) { const nextError = cause instanceof Error ? cause : new Error("Unable to submit operator review"); setError(nextError); throw nextError; }
    finally { setReviewSubmitting(false); }
  }, [getApi, runId]);

  const downloadReport = useCallback(async () => {
    if (!runId) throw new Error("A protected run is required");
    setError(null);
    try { const result = await getApi().getDownload(runId); window.location.assign(result.url); }
    catch (cause) { const nextError = cause instanceof Error ? cause : new Error("Unable to download report"); setError(nextError); throw nextError; }
  }, [getApi, runId]);

  return { runCreating, reviewSubmitting, review, error, createRun, submitReview, downloadReport };
}
