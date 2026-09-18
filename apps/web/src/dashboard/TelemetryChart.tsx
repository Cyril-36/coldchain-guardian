import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Snapshot } from "../types/contracts";

interface ChartPoint {
  observed_at: string;
  timestamp: number;
  sensor_a: number | null;
  sensor_b: number | null;
  sensor_a_event_id: string | null;
  sensor_b_event_id: string | null;
}

export interface SensorPoint {
  observed_at: string;
  timestamp: number;
  temperature: number | null;
  event_id: string | null;
}

export interface Gap {
  start: number;
  end: number;
  sensor: "A" | "B";
}

interface TelemetryChartProps {
  snapshot: Snapshot;
  highlightedRecordIds?: Set<string>;
  onRecordSelect?: (recordIds: string[]) => void;
}

function buildSeries(snapshot: Snapshot): ChartPoint[] {
  const byTime = new Map<number, ChartPoint>();
  for (const reading of snapshot.readings) {
    const timestamp = Date.parse(reading.observed_at);
    const point = byTime.get(timestamp) ?? {
      observed_at: reading.observed_at,
      timestamp,
      sensor_a: null,
      sensor_b: null,
      sensor_a_event_id: null,
      sensor_b_event_id: null,
    };
    const sensor = snapshot.sensors.find((item) => item.sensor_id === reading.sensor_id);
    if (sensor?.role === "reference") {
      point.sensor_a = reading.temperature_c;
      point.sensor_a_event_id = reading.event_id;
    }
    if (sensor?.role === "comparison") {
      point.sensor_b = reading.temperature_c;
      point.sensor_b_event_id = reading.event_id;
    }
    byTime.set(timestamp, point);
  }
  return [...byTime.values()].sort((a, b) => a.timestamp - b.timestamp);
}

export function buildSensorSeries(
  snapshot: Snapshot,
  role: "reference" | "comparison",
  gaps: Gap[],
): SensorPoint[] {
  const sensor = snapshot.sensors.find((item) => item.role === role);
  if (!sensor) return [];
  const readings = snapshot.readings
    .filter((reading) => reading.sensor_id === sensor.sensor_id)
    .sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
  const sensorGaps = gaps.filter((gap) => gap.sensor === (role === "reference" ? "A" : "B"));
  const points: SensorPoint[] = [];

  readings.forEach((reading, index) => {
    points.push({
      observed_at: reading.observed_at,
      timestamp: Date.parse(reading.observed_at),
      temperature: reading.temperature_c,
      event_id: reading.event_id,
    });
    if (index < readings.length - 1) {
      const start = Date.parse(reading.observed_at);
      const end = Date.parse(readings[index + 1].observed_at);
      if (sensorGaps.some((gap) => gap.start === start && gap.end === end)) {
        points.push({
          observed_at: new Date(start + (end - start) / 2).toISOString(),
          timestamp: start + (end - start) / 2,
          temperature: null,
          event_id: null,
        });
      }
    }
  });
  return points;
}

export function getSensorGaps(snapshot: Snapshot): Gap[] {
  return snapshot.sensors.flatMap((sensor) => {
    const readings = snapshot.readings
      .filter((reading) => reading.sensor_id === sensor.sensor_id)
      .sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
    const gaps: Gap[] = [];
    for (let index = 1; index < readings.length; index += 1) {
      const start = Date.parse(readings[index - 1].observed_at);
      const end = Date.parse(readings[index].observed_at);
      if ((end - start) / 1000 > snapshot.policy.max_gap_seconds) {
        gaps.push({ start, end, sensor: sensor.role === "reference" ? "A" : "B" });
      }
    }
    return gaps;
  });
}

function formatUtc(value: number) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(new Date(value));
}

function TooltipContent({
  active,
  payload,
  policy,
  events,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; dataKey?: string; value?: number | null; payload?: any }>;
  policy?: { min_c: number; max_c: number };
  events?: Snapshot["events"];
}) {
  if (!active || !payload?.length) return null;
  const itemA = payload.find((item) => item.name === "Sensor A" || item.dataKey === "sensor_a");
  const itemB = payload.find((item) => item.name === "Sensor B" || item.dataKey === "sensor_b");
  const firstPayload = payload[0].payload ?? {};
  const timestamp = firstPayload.timestamp ?? Date.now();
  const valA =
    itemA && itemA.value !== undefined
      ? itemA.value
      : firstPayload.sensor_a ?? (firstPayload.temperature !== undefined ? firstPayload.temperature : null);
  const valB = itemB && itemB.value !== undefined ? itemB.value : firstPayload.sensor_b ?? null;
  const eventIdA = itemA?.payload?.event_id ?? firstPayload.sensor_a_event_id ?? (firstPayload.event_id ?? null);
  const eventIdB = itemB?.payload?.event_id ?? firstPayload.sensor_b_event_id ?? null;

  const isExcursionA =
    policy && valA != null && (valA > policy.max_c || valA < policy.min_c);
  const isExcursionB =
    policy && valB != null && (valB > policy.max_c || valB < policy.min_c);

  const matchedEvent = events?.find(
    (e) => Math.abs(Date.parse(e.observed_at) - timestamp) < 60_000,
  );

  return (
    <div className="telemetry-tooltip">
      <div className="telemetry-tooltip__header">
        <span className="telemetry-tooltip__time">{formatUtc(timestamp)} UTC</span>
        {isExcursionA && (
          <span className="telemetry-tooltip__badge telemetry-tooltip__badge--excursion">
            EXCURSION
          </span>
        )}
      </div>

      <div className="telemetry-tooltip__row">
        <span className="telemetry-tooltip__sensor-label telemetry-tooltip__sensor-label--ref">
          Sensor A (Reference)
        </span>
        <span
          className={`telemetry-tooltip__value ${
            isExcursionA ? "telemetry-tooltip__value--excursion" : ""
          }`}
        >
          {valA == null ? "— (Gap)" : `${valA}°C`}
        </span>
      </div>

      <div className="telemetry-tooltip__row">
        <span className="telemetry-tooltip__sensor-label telemetry-tooltip__sensor-label--comp">
          Sensor B (Comparison)
        </span>
        <span
          className={`telemetry-tooltip__value ${
            isExcursionB ? "telemetry-tooltip__value--comp-excursion" : ""
          }`}
        >
          {valB == null ? "— (Gap)" : `${valB}°C`}
        </span>
      </div>

      {matchedEvent && (
        <div className="telemetry-tooltip__event">
          <span className="telemetry-tooltip__event-pin">●</span>
          <span>
            {matchedEvent.event_type.replace(/_/g, " ")}: <strong>{matchedEvent.value}</strong>
          </span>
        </div>
      )}

      {(eventIdA || eventIdB) && (
        <div className="telemetry-tooltip__meta">
          {eventIdA && <span>Record A: {eventIdA.slice(-8)}</span>}
          {eventIdB && <span>Record B: {eventIdB.slice(-8)}</span>}
        </div>
      )}
    </div>
  );
}

export function TelemetryChart({
  snapshot,
  highlightedRecordIds = new Set(),
  onRecordSelect,
}: TelemetryChartProps) {
  const series = useMemo(() => buildSeries(snapshot), [snapshot]);
  const gaps = useMemo(() => getSensorGaps(snapshot), [snapshot]);
  const sensorASeries = useMemo(() => buildSensorSeries(snapshot, "reference", gaps), [snapshot, gaps]);
  const sensorBSeries = useMemo(() => buildSensorSeries(snapshot, "comparison", gaps), [snapshot, gaps]);
  const minTime = series[0]?.timestamp ?? Date.parse(snapshot.cutoff_at);
  const maxTime = series.at(-1)?.timestamp ?? minTime;
  const eventMarkers = snapshot.events.filter((event) => event.event_type !== "vehicle_state");

  const minC = snapshot.policy.min_c;
  const maxC = snapshot.policy.max_c;

  return (
    <div className="telemetry-card">
      <div className="telemetry-card__header">
        <div>
          <div className="telemetry-card__eyebrow">PRIMARY EVIDENCE TIMELINE</div>
          <h2 className="telemetry-card__title">Temperature telemetry</h2>
          <p className="telemetry-card__subtitle">
            Deterministic dual-probe records · Configured policy band {minC}°C–{maxC}°C · UTC timeline
          </p>
        </div>
        <div className="telemetry-legend" aria-label="Chart legend">
          <span className="telemetry-legend__item">
            <span className="telemetry-legend__swatch telemetry-legend__swatch--sensor-a" />
            Sensor A (Reference)
          </span>
          <span className="telemetry-legend__item">
            <span className="telemetry-legend__swatch telemetry-legend__swatch--sensor-b" />
            Sensor B (Comparison)
          </span>
          <span className="telemetry-legend__item">
            <span className="telemetry-legend__swatch telemetry-legend__swatch--policy" />
            Policy band ({minC}°–{maxC}°C)
          </span>
          <span className="telemetry-legend__item">
            <span className="telemetry-legend__swatch telemetry-legend__swatch--excursion" />
            Excursion
          </span>
          <span className="telemetry-legend__item">
            <span className="telemetry-legend__swatch telemetry-legend__swatch--gap" />
            Data gap
          </span>
        </div>
      </div>

      <div className="telemetry-chart-container">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={series} margin={{ top: 24, right: 28, left: 6, bottom: 12 }}>
            <CartesianGrid stroke="#E2E8F0" strokeDasharray="3 3" vertical={false} />
            <XAxis
              dataKey="timestamp"
              type="number"
              domain={[minTime, maxTime]}
              tickFormatter={formatUtc}
              tick={{ fontSize: 11, fill: "#64748B", fontWeight: 500 }}
              axisLine={{ stroke: "#CBD5E1" }}
              tickLine={{ stroke: "#CBD5E1" }}
            />
            <YAxis
              domain={[0, "auto"]}
              tickFormatter={(value) => `${value}°C`}
              tick={{ fontSize: 11, fill: "#64748B", fontWeight: 500 }}
              axisLine={false}
              tickLine={false}
              width={46}
            />

            {/* Policy safe range band */}
            <ReferenceArea
              y1={minC}
              y2={maxC}
              fill="#E0F2FE"
              fillOpacity={0.45}
            />
            <ReferenceLine
              y={maxC}
              stroke="#0284C7"
              strokeDasharray="4 4"
              strokeWidth={1}
              label={{
                value: `Policy Max ${maxC}°C`,
                position: "insideTopRight",
                fill: "#0369A1",
                fontSize: 10,
                fontWeight: 600,
              }}
            />
            <ReferenceLine
              y={minC}
              stroke="#0284C7"
              strokeDasharray="4 4"
              strokeWidth={1}
              label={{
                value: `Policy Min ${minC}°C`,
                position: "insideBottomRight",
                fill: "#0369A1",
                fontSize: 10,
                fontWeight: 600,
              }}
            />

            {/* Per-sensor Telemetry Gaps */}
            {gaps.map((gap) => (
              <ReferenceArea
                key={`${gap.sensor}-${gap.start}-${gap.end}`}
                x1={gap.start}
                x2={gap.end}
                fill="#F1F5F9"
                fillOpacity={0.88}
                stroke="#94A3B8"
                strokeWidth={1}
                strokeDasharray="4 4"
                label={{
                  value: `DATA GAP (${gap.sensor})`,
                  position: "insideTop",
                  fill: "#475569",
                  fontSize: 10,
                  fontWeight: 700,
                }}
              />
            ))}

            {/* Anchored Event Markers (e.g. Door Opened) */}
            {eventMarkers.map((event) => {
              const isEventActive = highlightedRecordIds.has(event.event_id);
              const eventLabel =
                event.event_type === "door_state"
                  ? `Door ${event.value}`
                  : event.value;
              return (
                <ReferenceLine
                  key={event.event_id}
                  x={Date.parse(event.observed_at)}
                  stroke={isEventActive ? "#B91C1C" : "#D97706"}
                  strokeWidth={isEventActive ? 2.5 : 1.5}
                  strokeDasharray={isEventActive ? undefined : "4 4"}
                  label={{
                    value: `● ${eventLabel}`,
                    position: "insideTopLeft",
                    fontSize: 11,
                    fill: isEventActive ? "#991B1B" : "#B45309",
                    fontWeight: 700,
                  }}
                />
              );
            })}

            <Tooltip
              cursor={{ stroke: "#94A3B8", strokeWidth: 1, strokeDasharray: "2 2" }}
              content={
                <TooltipContent
                  policy={{ min_c: minC, max_c: maxC }}
                  events={snapshot.events}
                />
              }
            />

            {/* Reference Sensor Line */}
            <Line
              data={sensorASeries}
              type="monotone"
              dataKey="temperature"
              connectNulls={false}
              stroke="#0891B2"
              strokeWidth={2.5}
              dot={({ cx, cy, payload }) => {
                if (payload.temperature == null) return null;
                const isHighlighted =
                  payload.event_id && highlightedRecordIds.has(payload.event_id);
                const isExcursion =
                  payload.temperature > maxC || payload.temperature < minC;

                if (isHighlighted) {
                  return (
                    <g key={`dot-a-${payload.timestamp}`}>
                      <circle cx={cx} cy={cy} r={9} fill="#B91C1C" fillOpacity={0.25} />
                      <circle cx={cx} cy={cy} r={5.5} fill="#B91C1C" stroke="#FFFFFF" strokeWidth={2} />
                    </g>
                  );
                }
                if (isExcursion) {
                  return (
                    <circle
                      key={`dot-a-${payload.timestamp}`}
                      cx={cx}
                      cy={cy}
                      r={4.5}
                      fill="#DC2626"
                      stroke="#FEE2E2"
                      strokeWidth={1.5}
                    />
                  );
                }
                return (
                  <circle
                    key={`dot-a-${payload.timestamp}`}
                    cx={cx}
                    cy={cy}
                    r={2.5}
                    fill="#0891B2"
                  />
                );
              }}
              name="Sensor A"
            />

            {/* Comparison Sensor Line */}
            <Line
              data={sensorBSeries}
              type="monotone"
              dataKey="temperature"
              connectNulls={false}
              stroke="#64748B"
              strokeWidth={1.75}
              strokeDasharray="4 4"
              dot={({ cx, cy, payload }) => {
                if (payload.temperature == null) return null;
                const isHighlighted =
                  payload.event_id && highlightedRecordIds.has(payload.event_id);
                const isExcursion =
                  payload.temperature > maxC || payload.temperature < minC;

                if (isHighlighted) {
                  return (
                    <g key={`dot-b-${payload.timestamp}`}>
                      <circle cx={cx} cy={cy} r={8} fill="#B91C1C" fillOpacity={0.2} />
                      <circle cx={cx} cy={cy} r={5} fill="#B91C1C" stroke="#FFFFFF" strokeWidth={1.5} />
                    </g>
                  );
                }
                if (isExcursion) {
                  return (
                    <circle
                      key={`dot-b-${payload.timestamp}`}
                      cx={cx}
                      cy={cy}
                      r={3.5}
                      fill="#EA580C"
                      stroke="#FFEDD5"
                      strokeWidth={1}
                    />
                  );
                }
                return (
                  <circle
                    key={`dot-b-${payload.timestamp}`}
                    cx={cx}
                    cy={cy}
                    r={2}
                    fill="#64748B"
                  />
                );
              }}
              name="Sensor B"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Footer bar with policy notes, gaps, and evidence selection status */}
      <div className="telemetry-card__footer">
        <div className="telemetry-status-row">
          <span className="telemetry-status-pill">
            Policy: {minC}°C–{maxC}°C (Configured threshold)
          </span>
          {gaps.length > 0 && (
            <span className="telemetry-status-pill telemetry-status-pill--gap">
              {gaps.length} disjoint telemetry gap{gaps.length === 1 ? "" : "s"} detected
            </span>
          )}
          {highlightedRecordIds.size > 0 && (
            <span className="telemetry-status-pill telemetry-status-pill--active">
              Evidence selection highlighted on chart
            </span>
          )}
        </div>

        {eventMarkers.length > 0 && (
          <div className="telemetry-events-strip" aria-label="Telemetry events">
            <span className="telemetry-events-label">Timeline events:</span>
            {eventMarkers.map((event) => {
              const active = highlightedRecordIds.has(event.event_id);
              const label =
                event.event_type === "door_state"
                  ? `Door ${event.value}`
                  : event.event_type.replace(/_/g, " ");
              return (
                <button
                  key={event.event_id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onRecordSelect?.(active ? [] : [event.event_id])}
                  className={`telemetry-event-chip ${
                    active ? "telemetry-event-chip--active" : ""
                  }`}
                >
                  <span className="telemetry-event-chip__pin" aria-hidden="true">●</span>
                  <span className="telemetry-event-chip__text">
                    {label} · {formatUtc(Date.parse(event.observed_at))} UTC
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
