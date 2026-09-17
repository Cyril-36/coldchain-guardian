import { describe, expect, it } from "vitest";
import { demoSnapshot } from "./mockData";
import { getSensorGaps } from "./TelemetryChart";

describe("getSensorGaps", () => {
  it("does not classify a normal cadence as a gap", () => {
    expect(getSensorGaps(demoSnapshot)).toHaveLength(0);
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
});
