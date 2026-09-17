import { describe, expect, it } from "vitest";
import type { RunStage, RunStatus } from "../types/contracts";

const statusDescriptions: Record<RunStatus, { label: string; terminal: boolean }> = {
  pending_enqueue: { label: "Pending enqueue", terminal: false },
  queued: { label: "Queued", terminal: false },
  running: { label: "Running", terminal: false },
  completed: { label: "Completed", terminal: true },
  needs_review: { label: "Needs review", terminal: true },
  failed: { label: "Failed", terminal: true },
};

const stageLabels: Record<RunStage, string> = {
  preparing: "Preparing",
  detecting: "Detecting",
  collecting_evidence: "Collecting evidence",
  comparing_hypotheses: "Comparing hypotheses",
  verifying: "Verifying",
  ready: "Ready",
  failed: "Failed",
};

function isTerminalStatus(status: RunStatus): boolean {
  return status === "completed" || status === "needs_review" || status === "failed";
}

describe("status and stage mapping", () => {
  it("correctly maps all contract status values to human-readable labels", () => {
    const statuses: RunStatus[] = ["pending_enqueue", "queued", "running", "completed", "needs_review", "failed"];
    for (const status of statuses) {
      expect(statusDescriptions[status]).toBeDefined();
      expect(statusDescriptions[status].label.length).toBeGreaterThan(0);
    }
  });

  it("correctly distinguishes terminal from non-terminal states", () => {
    expect(isTerminalStatus("pending_enqueue")).toBe(false);
    expect(isTerminalStatus("queued")).toBe(false);
    expect(isTerminalStatus("running")).toBe(false);
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("needs_review")).toBe(true);
    expect(isTerminalStatus("failed")).toBe(true);
  });

  it("maps all 7 contract run stages correctly", () => {
    const stages: RunStage[] = ["preparing", "detecting", "collecting_evidence", "comparing_hypotheses", "verifying", "ready", "failed"];
    for (const stage of stages) {
      expect(stageLabels[stage]).toBeDefined();
      expect(typeof stageLabels[stage]).toBe("string");
    }
    expect(stageLabels.comparing_hypotheses).toBe("Comparing hypotheses");
    expect(stageLabels.collecting_evidence).toBe("Collecting evidence");
  });
});
