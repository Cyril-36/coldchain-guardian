import { describe, expect, it } from "vitest";
import { createRunAttempt } from "./runAttempt";

describe("runAttempt creation and retry key reuse", () => {
  it("reuses seed and idempotency key for an uncertain retry", () => {
    const first = createRunAttempt(null, "scenario-door-101");
    const retry = createRunAttempt(first, "scenario-door-101");

    expect(retry).toBe(first);
    expect(retry.scenarioId).toBe("scenario-door-101");
    expect(retry.seed).toBe(first.seed);
    expect(retry.idempotencyKey).toBe(first.idempotencyKey);
  });

  it("generates a distinct seed and idempotency key for a new run", () => {
    const first = createRunAttempt(null, "scenario-1");
    const next = createRunAttempt(null, "scenario-1");

    expect(next).not.toBe(first);
    expect(next.idempotencyKey).not.toBe(first.idempotencyKey);
    expect(typeof next.seed).toBe("number");
    expect(typeof next.idempotencyKey).toBe("string");
  });

  it("uses provided identity factory for deterministic testing", () => {
    const factory = () => ({ seed: 42, idempotencyKey: "fixed-key-123" });
    const attempt = createRunAttempt(null, "scenario-custom", factory);

    expect(attempt).toEqual({
      scenarioId: "scenario-custom",
      seed: 42,
      idempotencyKey: "fixed-key-123",
    });
  });
});
