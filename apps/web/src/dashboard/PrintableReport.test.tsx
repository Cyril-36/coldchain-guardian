import { describe, expect, it } from "vitest";
import { demoReport, demoRun, demoSnapshot } from "./mockData";
import { PrintableReport } from "./PrintableReport";

describe("PrintableReport", () => {
  it("renders the required investigation record sections", () => {
    const element = PrintableReport({ run: demoRun, snapshot: demoSnapshot, report: demoReport });
    const children = Array.isArray(element.props.children) ? element.props.children : [element.props.children];
    const text = JSON.stringify(children);
    expect(element.props.className).toBe("print-report");
    expect(element.props["aria-labelledby"]).toBe("print-report-title");
    expect(text).toContain("Measurements");
    expect(text).toContain("Investigation outcome");
    expect(text).toContain("Evidence");
    expect(text).toContain("Next checks");
    expect(text).toContain("Limitations & verification");
    expect(text).toContain("Operator review");
  });
});
