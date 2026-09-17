import { describe, expect, it } from "vitest";
import { demoReport, demoRun, demoSnapshot } from "./mockData";
import { PrintableReport, resolveReview } from "./PrintableReport";

describe("PrintableReport", () => {
  it("renders the required investigation record sections", () => {
    const element = PrintableReport({ run: demoRun, snapshot: demoSnapshot, report: demoReport });
    const children = Array.isArray(element.props.children) ? element.props.children : [element.props.children];
    const text = JSON.stringify(children);
    expect(element.props.className).toBe("print-report");
    expect(element.props["aria-labelledby"]).toBe("print-report-title");
    for (const section of ["Measurements", "Investigation outcome", "Evidence", "Next checks", "Limitations & verification", "Operator review"]) {
      expect(text).toContain(section);
    }
  });

  it("prefers a newly submitted review over the stale run payload", () => {
    const review = {
      review_id: "00000000-0000-0000-0000-000000009999",
      run_id: demoRun.run_id,
      report_id: demoReport.report_id,
      decision: "acknowledged" as const,
      note: "Reviewed after the live submission.",
      actor_sub: "operator-test",
      reviewed_at: "2026-09-17T12:05:00Z",
    };

    expect(resolveReview(demoRun.review, review)).toBe(review);
  });
});
