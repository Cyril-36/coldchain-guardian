import { useCallback, useState } from "react";
import { ApiClient } from "../api/client";
import { createApiEndpoints } from "../api/endpoints";
import type { CreateReviewRequest, Review } from "../types/contracts";
import { useAuth } from "../auth/AuthProvider";

export function useOperatorActions(runId: string | null) {
  const auth = useAuth();
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [review, setReview] = useState<Review | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const submitReview = useCallback(async (request: CreateReviewRequest) => {
    if (!runId || !auth.authenticated) throw new Error("Operator session is required");
    setReviewSubmitting(true);
    setError(null);
    try {
      const baseUrl = import.meta.env.VITE_API_BASE_URL;
      if (!baseUrl) throw new Error("API base URL is not configured");
      const client = new ApiClient({ baseUrl, tokenProvider: auth.getAccessToken });
      const result = await createApiEndpoints(client).submitReview(runId, request);
      setReview(result);
      return result;
    } catch (cause) {
      const nextError = cause instanceof Error ? cause : new Error("Unable to submit operator review");
      setError(nextError);
      throw nextError;
    } finally {
      setReviewSubmitting(false);
    }
  }, [auth.authenticated, auth.getAccessToken, runId]);

  const downloadReport = useCallback(async () => {
    if (!runId || !auth.authenticated) throw new Error("Operator session is required");
    setError(null);
    try {
      const baseUrl = import.meta.env.VITE_API_BASE_URL;
      if (!baseUrl) throw new Error("API base URL is not configured");
      const client = new ApiClient({ baseUrl, tokenProvider: auth.getAccessToken });
      const result = await createApiEndpoints(client).getDownload(runId);
      window.location.assign(result.url);
    } catch (cause) {
      const nextError = cause instanceof Error ? cause : new Error("Unable to download report");
      setError(nextError);
      throw nextError;
    }
  }, [auth.authenticated, auth.getAccessToken, runId]);

  return { reviewSubmitting, review, error, submitReview, downloadReport };
}
