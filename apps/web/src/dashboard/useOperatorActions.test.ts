import { describe, expect, it } from "vitest";
import { createRunAttempt } from "./runAttempt";

describe("createRunAttempt", () => {
  const identity = () => ({ seed: 12345, idempotencyKey: "00000000-0000-0000-0000-000000009999" });

  it("reuses seed and idempotency key for an uncertain retry", () => {
    const first = createRunAttempt(null, "scenario-1", identity);
    const retry = createRunAttempt(first, "scenario-1", () => ({ seed: 67890, idempotencyKey: "00000000-0000-0000-0000-000000008888" }));

    expect(retry).toBe(first);
    expect(retry.scenarioId).toBe("scenario-1");
    expect(retry.seed).toBe(12345);
    expect(retry.idempotencyKey).toBe("00000000-0000-0000-0000-000000009999");
  });

  it("creates a fresh request after a completed request is cleared", () => {
    const first = createRunAttempt(null, "scenario-1", identity);
    const next = createRunAttempt(null, "scenario-1", () => ({ seed: 67890, idempotencyKey: "00000000-0000-0000-0000-000000008888" }));

    expect(next).not.toBe(first);
    expect(next.seed).toBe(67890);
    expect(next.idempotencyKey).toBe("00000000-0000-0000-0000-000000008888");
  });
});
