import { useMemo } from "react";
import { CartesianGrid, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Snapshot } from "../types/contracts";

interface ChartPoint {
  observed_at: string; timestamp: number; sensor_a: number | null; sensor_b: number | null;
  sensor_a_event_id: string | null; sensor_b_event_id: string | null;
}
interface Gap { start: number; end: number; sensor: "A" | "B"; }
interface TelemetryChartProps { snapshot: Snapshot; highlightedRecordIds?: Set<string>; onRecordSelect?: (recordIds: string[]) => void; }

function buildSeries(snapshot: Snapshot): ChartPoint[] {
  const byTime = new Map<number, ChartPoint>();
  for (const reading of snapshot.readings) {
    const timestamp = Date.parse(reading.observed_at);
    const point = byTime.get(timestamp) ?? { observed_at: reading.observed_at, timestamp, sensor_a: null, sensor_b: null, sensor_a_event_id: null, sensor_b_event_id: null };
    const sensor = snapshot.sensors.find((item) => item.sensor_id === reading.sensor_id);
    if (sensor?.role === "reference") { point.sensor_a = reading.temperature_c; point.sensor_a_event_id = reading.event_id; }
    if (sensor?.role === "comparison") { point.sensor_b = reading.temperature_c; point.sensor_b_event_id = reading.event_id; }
    byTime.set(timestamp, point);
  }
  return [...byTime.values()].sort((a, b) => a.timestamp - b.timestamp);
}

function getSensorGaps(snapshot: Snapshot): Gap[] {
  return snapshot.sensors.flatMap((sensor) => {
    const readings = snapshot.readings.filter((reading) => reading.sensor_id === sensor.sensor_id).sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
    const gaps: Gap[] = [];
    for (let index = 1; index < readings.length; index += 1) {
      const start = Date.parse(readings[index - 1].observed_at); const end = Date.parse(readings[index].observed_at);
      if ((end - start) / 1000 > snapshot.policy.max_gap_seconds) gaps.push({ start, end, sensor: sensor.role === "reference" ? "A" : "B" });
    }
    return gaps;
  });
}

function formatUtc(value: number) { return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(value)); }

function TooltipContent({ active, payload }: { active?: boolean; payload?: Array<{ payload: ChartPoint }> }) {
  if (!active || !payload?.length) return null;
  const point = payload[0].payload;
  return <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
    <p className="font-semibold text-slate-950">{formatUtc(point.timestamp)} UTC</p>
    <p className="mt-1 text-cyan-700">Sensor A: {point.sensor_a == null ? "—" : `${point.sensor_a}°C`}</p>
    <p className="text-slate-700">Sensor B: {point.sensor_b == null ? "—" : `${point.sensor_b}°C`}</p>
    {point.sensor_a_event_id && <p className="mt-1 text-[10px] text-slate-400">A record {point.sensor_a_event_id}</p>}
    {point.sensor_b_event_id && <p className="text-[10px] text-slate-400">B record {point.sensor_b_event_id}</p>}
  </div>;
}

export function TelemetryChart({ snapshot, highlightedRecordIds = new Set(), onRecordSelect }: TelemetryChartProps) {
  const series = useMemo(() => buildSeries(snapshot), [snapshot]);
  const gaps = useMemo(() => getSensorGaps(snapshot), [snapshot]);
  const minTime = series[0]?.timestamp ?? Date.parse(snapshot.cutoff_at); const maxTime = series.at(-1)?.timestamp ?? minTime;
  const eventMarkers = snapshot.events.filter((event) => event.event_type !== "vehicle_state");

  return <div className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm">
    <div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="text-lg font-semibold text-slate-950">Temperature telemetry</h2><p className="mt-1 text-[11px] text-slate-500">Configured band · {snapshot.policy.min_c}°C–{snapshot.policy.max_c}°C · both sensors · UTC</p></div><div className="flex gap-4 text-[11px] font-semibold text-slate-600"><span className="text-cyan-700">● Sensor A</span><span className="text-slate-700">● Sensor B</span><span>▰ Policy band</span></div></div>
    <div className="mt-4 h-[300px] rounded-md border border-slate-200 bg-slate-50 p-2"><ResponsiveContainer width="100%" height="100%"><LineChart data={series} margin={{ top: 20, right: 20, left: 4, bottom: 8 }}>
      <CartesianGrid stroke="#E2E8F0" vertical={false} />
      <XAxis dataKey="timestamp" type="number" domain={[minTime, maxTime]} tickFormatter={formatUtc} tick={{ fontSize: 10, fill: "#94A3B8" }} axisLine={{ stroke: "#CBD5E1" }} tickLine={false} />
      <YAxis domain={[0, "auto"]} tickFormatter={(value) => `${value}°C`} tick={{ fontSize: 10, fill: "#94A3B8" }} axisLine={false} tickLine={false} width={42} />
      <ReferenceArea y1={snapshot.policy.min_c} y2={snapshot.policy.max_c} fill="#EAF5F7" fillOpacity={1} />
      {gaps.map((gap) => <ReferenceArea key={`${gap.sensor}-${gap.start}-${gap.end}`} x1={gap.start} x2={gap.end} fill="#CBD5E1" fillOpacity={0.45} label={{ value: `Sensor ${gap.sensor} gap`, position: "insideTop" }} />)}
      {eventMarkers.map((event) => <ReferenceLine key={event.event_id} x={Date.parse(event.observed_at)} stroke="#B45309" strokeDasharray="4 4" label={{ value: event.value, position: "insideTop", fontSize: 10, fill: "#B45309" }} />)}
      <Tooltip content={<TooltipContent />} />
      <Line type="monotone" dataKey="sensor_a" connectNulls={false} stroke="#0891B2" strokeWidth={2.5} dot={({ cx, cy, payload }) => <circle cx={cx} cy={cy} r={payload.sensor_a_event_id && highlightedRecordIds.has(payload.sensor_a_event_id) ? 6 : 3} fill={payload.sensor_a_event_id && highlightedRecordIds.has(payload.sensor_a_event_id) ? "#B91C1C" : "#0891B2"} />} name="Sensor A" />
      <Line type="monotone" dataKey="sensor_b" connectNulls={false} stroke="#475569" strokeWidth={2} dot={({ cx, cy, payload }) => <circle cx={cx} cy={cy} r={payload.sensor_b_event_id && highlightedRecordIds.has(payload.sensor_b_event_id) ? 6 : 3} fill={payload.sensor_b_event_id && highlightedRecordIds.has(payload.sensor_b_event_id) ? "#B91C1C" : "#475569"} />} name="Sensor B" />
    </LineChart></ResponsiveContainer></div>
    <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px]"><span className="font-semibold text-slate-600">Policy band is sourced from the snapshot</span>{gaps.length > 0 && <span className="text-slate-500">{gaps.length} per-sensor telemetry gap{gaps.length === 1 ? "" : "s"}</span>}{highlightedRecordIds.size > 0 && <span className="text-amber-700">Evidence selection highlighted on chart</span>}</div>
    {eventMarkers.length > 0 && <div className="mt-3 flex flex-wrap gap-2" aria-label="Telemetry events">{eventMarkers.map((event) => { const active = highlightedRecordIds.has(event.event_id); return <button key={event.event_id} type="button" onClick={() => onRecordSelect?.([event.event_id])} className={`rounded-md border px-2.5 py-1.5 text-[10px] font-semibold transition ${active ? "border-amber-400 bg-amber-50 text-amber-800" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}>{event.event_type.replace("_", " ")} · {formatUtc(Date.parse(event.observed_at))}</button>; })}</div>}
  </div>;
}
