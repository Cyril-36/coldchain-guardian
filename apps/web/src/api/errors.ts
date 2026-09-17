import type {
  ApiErrorBody,
  ApiErrorEnvelope,
} from "../types/contracts";

export class ApiClientError extends Error {
  readonly status: number;
  readonly details: ApiErrorBody | null;

  constructor(status: number, details: ApiErrorBody | null, fallbackMessage?: string) {
    super(details?.message ?? fallbackMessage ?? `Request failed with status ${status}`);
    this.name = "ApiClientError";
    this.status = status;
    this.details = details;
  }

  get retryable(): boolean {
    return this.details?.retryable ?? this.status >= 500;
  }
}

export function isApiErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (!value || typeof value !== "object") return false;
  const error = (value as Record<string, unknown>).error;
  if (!error || typeof error !== "object") return false;
  const body = error as Record<string, unknown>;
  return (
    typeof body.code === "string" &&
    typeof body.message === "string" &&
    typeof body.request_id === "string" &&
    typeof body.retryable === "boolean"
  );
}
