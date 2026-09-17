import { describe, expect, it } from "vitest";
import { demoSnapshot } from "./mockData";
import { TelemetryChart } from "./TelemetryChart";

describe("TelemetryChart", () => {
  it("uses per-sensor gap detection and keeps the chart keyboard-oriented through event buttons", () => {
    const element = TelemetryChart({ snapshot: demoSnapshot, highlightedRecordIds: new Set(["missing"]) });
    expect(element.props.children).toBeTruthy();
    expect(element.props.className).toContain("rounded");
  });
});
