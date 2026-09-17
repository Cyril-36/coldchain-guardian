import { describe, expect, it } from "vitest";
import { demoSnapshot } from "./mockData";
import { buildSensorSeries, getSensorGaps } from "./TelemetryChart";

describe("telemetry gap handling", () => {
  it("does not classify readings as gaps when cadence is within policy", () => {
    const snapshot = structuredClone(demoSnapshot);
    snapshot.policy.max_gap_seconds = 15 * 60;
    expect(getSensorGaps(snapshot)).toHaveLength(0);
  });

  it("tracks gaps independently for each sensor", () => {
    const snapshot = structuredClone(demoSnapshot);
    const reference = snapshot.sensors.find((sensor) => sensor.role === "reference");
    if (!reference) throw new Error("fixture reference sensor missing");
    const referenceReadings = snapshot.readings.filter((reading) => reading.sensor_id === reference.sensor_id).sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
    if (referenceReadings.length < 2) throw new Error("fixture reference readings missing");
    const second = referenceReadings[1];
    second.observed_at = new Date(Date.parse(referenceReadings[0].observed_at) + (snapshot.policy.max_gap_seconds + 60) * 1000).toISOString();
    const gaps = getSensorGaps(snapshot);
    expect(gaps).toEqual(expect.arrayContaining([expect.objectContaining({ sensor: "A" })]));
  });

  it("inserts a null point into the affected sensor series so the line cannot bridge the gap", () => {
    const gaps = getSensorGaps(demoSnapshot);
    const sensorA = buildSensorSeries(demoSnapshot, "reference", gaps);
    const gap = gaps.find((item) => item.sensor === "A");
    if (!gap) throw new Error("fixture reference gap missing");
    const breakPoint = sensorA.find((point) => point.temperature === null);

    expect(breakPoint).toBeDefined();
    expect(breakPoint?.timestamp).toBe((gap.start + gap.end) / 2);
    expect(sensorA.some((point) => point.timestamp === gap.start && point.temperature !== null)).toBe(true);
    expect(sensorA.some((point) => point.timestamp === gap.end && point.temperature !== null)).toBe(true);
  });
});
