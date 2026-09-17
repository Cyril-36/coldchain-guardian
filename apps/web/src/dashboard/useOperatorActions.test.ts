import { describe, expect, it } from "vitest";
import { createRunAttempt } from "./useOperatorActions";

describe("createRunAttempt", () => {
  it("reuses seed and idempotency key for an uncertain retry", () => {
    const first = createRunAttempt(null, "scenario-1");
    const retry = createRunAttempt(first, "scenario-1");

    expect(retry).toBe(first);
    expect(retry.scenarioId).toBe("scenario-1");
    expect(retry.seed).toBe(first.seed);
    expect(retry.idempotencyKey).toBe(first.idempotencyKey);
  });

  it("does not reuse a completed request for a new run", () => {
    const first = createRunAttempt(null, "scenario-1");
    const next = createRunAttempt(null, "scenario-1");

    expect(next).not.toBe(first);
    expect(next.idempotencyKey).not.toBe(first.idempotencyKey);
    expect(next.seed).not.toBe(first.seed);
  });
});
