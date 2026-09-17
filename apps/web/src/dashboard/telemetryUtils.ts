import type { Reading, Snapshot } from "../types/contracts";

export interface TelemetryGap { start: number; end: number }

export function getTelemetryGaps(readings: Reading[], maxGapSeconds: number): TelemetryGap[] {
  const timestamps = [...new Set(readings.map((reading) => Date.parse(reading.observed_at)))].sort((a, b) => a - b);
  const gaps: TelemetryGap[] = [];
  for (let index = 1; index < timestamps.length; index += 1) {
    if ((timestamps[index] - timestamps[index - 1]) / 1000 > maxGapSeconds) gaps.push({ start: timestamps[index - 1], end: timestamps[index] });
  }
  return gaps;
}

export function sensorRole(snapshot: Snapshot, sensorId: string) {
  return snapshot.sensors.find((sensor) => sensor.sensor_id === sensorId)?.role;
}
