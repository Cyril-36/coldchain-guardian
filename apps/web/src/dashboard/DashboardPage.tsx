import { useMemo } from "react";
import { demoReport, demoRun, demoSnapshot } from "./mockData";
import type { Reading } from "../types/contracts";

const stages = [
  ["Preparing", "Complete"],
  ["Detecting", "Complete"],
  ["Collecting evidence", "Complete"],
  ["Comparing hypotheses", "Complete"],
  ["Verifying", "Complete"],
  ["Ready", "Current"],
];

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(value));
}

function MetricCard({ label, value, detail, tone }: { label: string; value: string; detail: string; tone?: "critical" | "review" | "info" }) {
  return (
    <article className="rounded-[10px] border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-semibold text-slate-600">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">{value}</p>
      <p className={`mt-1 text-[11px] font-medium ${tone === "critical" ? "text-red-700" : tone === "review" ? "text-amber-700" : "text-cyan-700"}`}>{detail}</p>
    </article>
  );
}

function TelemetryChart() {
  const { readings, policy } = demoSnapshot;
  const width = 1000;
  const height = 270;
  const pad = { left: 52, right: 24, top: 24, bottom: 38 };
  const range = Math.max(10, policy.max_c + 2);
  const min = 0;
  const x = (i: number) => pad.left + (i / Math.max(1, readings.length - 1)) * (width - pad.left - pad.right);
  const y = (v: number) => pad.top + ((range - v) / (range - min)) * (height - pad.top - pad.bottom);
  const series = useMemo(() => readings.filter((_, i) => i % 2 === 0), [readings]);
  const points = series.map((r, i) => `${x(i * 2)},${y(r.temperature_c)}`).join(" ");
  const policyTop = y(policy.max_c);
  const policyBottom = y(policy.min_c);

  return (
    <div className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-950">Temperature telemetry</h2>
          <p className="mt-1 text-[11px] text-slate-500">Configured band · {policy.min_c}°C–{policy.max_c}°C · both sensors · UTC</p>
        </div>
        <div className="flex gap-4 text-[11px] font-semibold text-slate-600"><span className="text-cyan-700">● Sensor A</span><span className="text-slate-700">● Sensor B</span><span>▰ Policy band</span></div>
      </div>
      <div className="mt-4 overflow-x-auto rounded-md border border-slate-200 bg-slate-50">
        <svg viewBox={`0 0 ${width} ${height}`} className="min-w-[760px] w-full" role="img" aria-label="Temperature telemetry chart with configured policy band and sensor readings">
          <rect x={pad.left} y={policyTop} width={width - pad.left - pad.right} height={policyBottom - policyTop} fill="#EAF5F7" />
          {[0, 2, 4, 6, 8, 10].map(v => <g key={v}><line x1={pad.left} x2={width-pad.right} y1={y(v)} y2={y(v)} stroke="#E2E8F0" /><text x={12} y={y(v)+4} fontSize="10" fill="#94A3B8">{v}°C</text></g>)}
          <polyline points={points} fill="none" stroke="#0891B2" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
          {series.map((r, i) => <circle key={r.event_id} cx={x(i*2)} cy={y(r.temperature_c)} r="3.5" fill={r.temperature_c > policy.max_c ? "#B91C1C" : "#0891B2"} />)}
          <line x1={x(6)} x2={x(6)} y1={28} y2={height-pad.bottom} stroke="#B45309" strokeDasharray="4 4" />
          <rect x={x(6)-48} y={18} width="96" height="24" rx="6" fill="#FFF7E6" stroke="#E8D2A8" />
          <text x={x(6)-39} y={34} fontSize="10" fontWeight="600" fill="#B45309">Door opened · 12:18</text>
          <line x1={x(15)} x2={x(15)} y1={height-60} y2={height-pad.bottom} stroke="#94A3B8" strokeDasharray="3 3" />
          <text x={x(15)-30} y={height-12} fontSize="10" fill="#475569">Gap · 4 min</text>
          <text x={pad.left} y={height-12} fontSize="10" fill="#94A3B8">12:00</text>
          <text x={width-pad.right-42} y={height-12} fontSize="10" fill="#94A3B8">14:32</text>
        </svg>
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-[11px]"><span className="font-semibold text-red-700">Peak observed at 9.4°C</span><span className="text-slate-500">Estimated duration may be incomplete because the window contains a telemetry gap.</span></div>
    </div>
  );
}

export function DashboardPage() {
  const report = demoReport;
  const finding = report.hypotheses.find(h => h.hypothesis === report.primary_hypothesis);

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <div className="mx-auto max-w-[1440px] p-5 lg:p-8">
        <header className="flex flex-wrap items-center gap-4 rounded-[10px] border border-slate-200 bg-white px-6 py-4 shadow-sm">
          <div className="min-w-[240px] flex-1"><h1 className="text-xl font-semibold">ColdChain Guardian</h1><p className="text-xs text-slate-400">Simulated shipment · AWS-backed processing</p></div>
          <label className="text-xs font-semibold text-slate-600">Demo case <select className="ml-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-normal text-slate-950"><option>Door exposure · Run 1042</option></select></label>
          <button className="rounded-md bg-[#123B5D] px-4 py-2.5 text-xs font-semibold text-white hover:bg-[#0F304C]">Run investigation</button>
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-50 text-xs font-semibold text-[#123B5D]">NG</div>
        </header>

        <div className="flex flex-wrap gap-x-6 gap-y-2 px-1 py-3 text-xs"><span className="font-semibold text-slate-600">Shipment SHP-2048</span><span className="text-slate-600">Policy CC-2.4 · 2°C–8°C</span><span className="text-slate-400">Cutoff 17 Sep 2026 · 14:32 UTC</span><span className="rounded px-2 py-1 font-semibold text-amber-700 bg-amber-50">SIMULATED</span></div>

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard label="Observed peak" value="9.4°C" detail="Above configured limit" tone="critical" />
          <MetricCard label="Estimated time out of range" value="18 min" detail="Censored at window edge" tone="review" />
          <MetricCard label="Data coverage" value="96%" detail="1 gap · 4 min" tone="info" />
          <MetricCard label="Review status" value="Needs review" detail="Evidence ready" tone="review" />
        </section>

        <section className="mt-3"><TelemetryChart /></section>

        <section className="mt-3 grid gap-3 lg:grid-cols-[0.9fr_1fr_1.05fr]">
          <article className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-semibold">Investigation stages</h2><div className="mt-5 space-y-3">{stages.map(([name,status]) => <div key={name} className="flex items-center gap-3"><span className="h-2.5 w-2.5 rounded-full bg-green-700" /><span className="flex-1 text-xs font-semibold">{name}</span><span className="text-[11px] text-green-700">{status}</span></div>)}</div></article>
          <article className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-semibold">Main finding</h2><div className="mt-4 rounded-lg bg-slate-50 p-4"><p className="text-sm font-semibold text-red-700">Temperature exceeded the configured limit</p><p className="mt-2 text-xs leading-5 text-slate-600">Possible explanation: {finding?.explanation} Evidence supports the hypothesis but does not establish disposition.</p></div><div className="mt-4"><h3 className="text-xs font-semibold text-slate-600">Next checks</h3><ul className="mt-2 space-y-2 text-xs">{report.next_checks.map(check => <li key={check.code}>• {check.reason}</li>)}</ul></div></article>
          <article className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm"><h2 className="text-lg font-semibold">Supporting & conflicting evidence</h2><div className="mt-4 space-y-2">{["Door event · 12:18 UTC · opened for 11 min|Supports door exposure|green","Temperature peak · 9.4°C · Sensor A|Supports excursion|green","Sensor B · 7.8°C · same interval|Conflicting / lower magnitude|amber","Data gap · 13:02–13:06 UTC|Limits duration estimate|amber"].map(item => { const [title, detail, tone] = item.split("|"); return <button key={title} className="w-full rounded-md bg-slate-50 p-3 text-left hover:bg-slate-100"><p className="text-[11px] font-semibold">{title}</p><p className="mt-1 text-[11px] text-slate-600">{detail}</p><p className={`mt-1 text-[10px] font-semibold ${tone === "green" ? "text-green-700" : "text-amber-700"}`}>{detail}</p></button>; })}</div></article>
        </section>

        <section className="mt-3 grid gap-3 lg:grid-cols-[2fr_1fr]">
          <article className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm"><div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-base font-semibold">Operator review</h2><p className="text-[11px] text-slate-500">Add a note and acknowledge or request more evidence.</p></div><div className="flex gap-2"><button className="rounded-md bg-[#123B5D] px-3 py-2 text-[11px] font-semibold text-white">Acknowledge review</button><button className="rounded-md border border-slate-200 px-3 py-2 text-[11px] font-semibold text-[#123B5D]">Request evidence</button></div></div><textarea maxLength={1000} placeholder="Add review note…" className="mt-3 h-14 w-full resize-none rounded-md border border-slate-200 bg-slate-50 p-3 text-xs outline-none focus:border-slate-400" /></article>
          <article className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center justify-between gap-3"><div><h2 className="text-base font-semibold">Report ready</h2><p className="mt-1 text-[10px] font-medium text-green-700">Evidence references and measurements checked</p></div><button className="rounded-md bg-[#123B5D] px-3 py-2 text-[11px] font-semibold text-white">Download</button></div><p className="mt-3 text-[10px] text-slate-500">IDs, request IDs and raw service names live in expandable diagnostics.</p></article>
        </section>
        <p className="mt-4 text-[10px] text-slate-400">Run {demoRun.run_id} · Last updated {formatTime(demoRun.completed_at!)} UTC</p>
      </div>
    </main>
  );
}
