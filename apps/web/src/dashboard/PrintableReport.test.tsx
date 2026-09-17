import { describe, expect, it } from "vitest";
import { demoReport, demoRun, demoSnapshot } from "./mockData";
import { PrintableReport } from "./PrintableReport";

describe("PrintableReport", () => {
  it("renders the required investigation record sections", () => {
    const element = PrintableReport({ run: demoRun, snapshot: demoSnapshot, report: demoReport });
    expect(element.props.className).toBe("print-report");
    expect(element.props["aria-labelledby"]).toBe("print-report-title");
    expect(element.props.children).toBeTruthy();
  });
});
