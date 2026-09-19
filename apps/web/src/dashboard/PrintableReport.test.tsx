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
    for (const section of ["Measurements", "Investigation outcome", "Evidence", "Next checks", "Limitations & verification", "Operator review"]) {
      expect(text).toContain(section);
    }
  });

  it("selects reference sensor and formats sub-minute durations without flooring to 0 min", () => {
    const customSnapshot = {
      ...demoSnapshot,
      sensors: [demoSnapshot.sensors[1], demoSnapshot.sensors[0]],
    };
    const customReport = {
      ...demoReport,
      measurements: [
        {
          ...demoReport.measurements[0],
          sensor_id: demoSnapshot.sensors[1].sensor_id,
          observed_max_c: 19.0,
          estimated_out_of_range_seconds: 500,
        },
        {
          ...demoReport.measurements[0],
          sensor_id: demoSnapshot.sensors[0].sensor_id,
          observed_max_c: 9.1,
          estimated_out_of_range_seconds: 30,
        },
      ],
    };

    const element = PrintableReport({ run: demoRun, snapshot: customSnapshot, report: customReport });
    const text = JSON.stringify(element);
    expect(text).toContain("9.1°C");
    expect(text).toContain("30s");
    expect(text).not.toContain("0 min");
  });
});
