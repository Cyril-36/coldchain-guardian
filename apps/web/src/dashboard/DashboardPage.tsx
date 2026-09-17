import { useMemo } from "react";
import { demoReport, demoRun, demoSnapshot } from "./mockData";
import "./dashboard.css";

const stages = [
  ["Preparing", "Complete"],
  ["Detecting", "Complete"],
  ["Collecting evidence", "Complete"],
  ["Comparing hypotheses", "Complete"],
  ["Verifying", "Complete"],
  ["Ready", "Current"],
] as const;

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(value));
}

function MetricCard({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: "critical" | "review" | "info" }) {
  return <article className="metric-card"><p className="eyebrow">{label}</p><p className="metric-value">{value}</p><p className={`metric-detail ${tone}`}>{detail}</p></article>;
}

function TelemetryChart() {
  const { readings, policy } = demoSnapshot;
  const width = 1000;
  const height = 270;
  const pad = { left: 52, right: 24, top: 24, bottom: 38 };
  const range = Math.max(10, policy.max_c + 2);
  const x = (i: number) => pad.left + (i / Math.max(1, readings.length - 1)) * (width - pad.left - pad.right);
  const y = (v: number) => pad.top + ((range - v) / range) * (height - pad.top - pad.bottom);
  const series = useMemo(() => readings.filter((_, i) => i % 2 === 0), [readings]);
  const points = series.map((r, i) => `${x(i * 2)},${y(r.temperature_c)}`).join(" ");
  const policyTop = y(policy.max_c);
  const policyBottom = y(policy.min_c);

  return <div className="card telemetry-card">
    <div className="panel-heading"><div><h2>Temperature telemetry</h2><p>Configured band · {policy.min_c}°C–{policy.max_c}°C · both sensors · UTC</p></div><div className="legend"><span className="sensor-a">● Sensor A</span><span className="sensor-b">● Sensor B</span><span>▰ Policy band</span></div></div>
    <div className="chart-shell"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Temperature telemetry chart with configured policy band and sensor readings">
      <rect x={pad.left} y={policyTop} width={width - pad.left - pad.right} height={policyBottom - policyTop} className="policy-band" />
      {[0, 2, 4, 6, 8, 10].map(v => <g key={v}><line x1={pad.left} x2={width - pad.right} y1={y(v)} y2={y(v)} className="gridline" /><text x="12" y={y(v) + 4} className="axis-label">{v}°C</text></g>)}
      <polyline points={points} className="series-a" />
      {series.map((r, i) => <circle key={r.event_id} cx={x(i * 2)} cy={y(r.temperature_c)} r="3.5" className={r.temperature_c > policy.max_c ? "point-critical" : "point-a"} />)}
      <line x1={x(6)} x2={x(6)} y1="28" y2={height - pad.bottom} className="event-line" />
      <rect x={x(6) - 48} y="18" width="96" height="24" rx="6" className="event-label-bg" /><text x={x(6) - 39} y="34" className="event-label">Door opened · 12:18</text>
      <line x1={x(15)} x2={x(15)} y1={height - 60} y2={height - pad.bottom} className="gap-line" /><text x={x(15) - 30} y={height - 12} className="gap-label">Gap · 4 min</text>
      <text x={pad.left} y={height - 12} className="axis-label">12:00</text><text x={width - pad.right - 42} y={height - 12} className="axis-label">14:32</text>
    </svg></div>
    <div className="chart-note"><span>Peak observed at 9.4°C</span><p>Estimated duration may be incomplete because the window contains a telemetry gap.</p></div>
  </div>;
}

export function DashboardPage() {
  const report = demoReport;
  const finding = report.hypotheses.find(h => h.hypothesis === report.primary_hypothesis);
  const evidence = [
    ["Door event", "12:18 UTC · opened for 11 min", "Supports door exposure", "green"],
    ["Temperature peak", "9.4°C · Sensor A", "Supports excursion", "green"],
    ["Sensor B", "7.8°C · same interval", "Conflicting / lower magnitude", "amber"],
    ["Data gap", "13:02–13:06 UTC", "Limits duration estimate", "amber"],
  ] as const;

  return <main className="app-shell"><div className="dashboard">
    <header className="header card"><div className="brand"><h1>ColdChain Guardian</h1><p>Simulated shipment · AWS-backed processing</p></div><div className="header-spacer" /><label className="case-label">Demo case<select defaultValue="door"><option value="door">Door exposure · Run 1042</option></select></label><button className="btn btn-primary">Run investigation</button><div className="avatar" aria-label="Operator">NG</div></header>
    <div className="context"><strong>Shipment SHP-2048</strong><span>Policy CC-2.4 · {demoSnapshot.policy.min_c}°C–{demoSnapshot.policy.max_c}°C</span><span className="muted">Cutoff 17 Sep 2026 · 14:32 UTC</span><span className="simulated">SIMULATED</span></div>
    <section className="metric-grid"><MetricCard label="Observed peak" value="9.4°C" detail="Above configured limit" tone="critical" /><MetricCard label="Estimated time out of range" value="18 min" detail="Censored at window edge" tone="review" /><MetricCard label="Data coverage" value="96%" detail="1 gap · 4 min" tone="info" /><MetricCard label="Review status" value="Needs review" detail="Evidence ready" tone="review" /></section>
    <TelemetryChart />
    <section className="lower-grid">
      <article className="card stages"><h2>Investigation stages</h2><div className="stage-list">{stages.map(([name, status], index) => <div className="stage-row" key={name}><span className={`stage-dot ${index === stages.length - 1 ? "current" : "complete"}`} /><span className="stage-name">{name}</span><span className={`stage-status ${index === stages.length - 1 ? "current-text" : "complete-text"}`}>{status}</span></div>)}</div></article>
      <article className="card finding"><h2>Main finding</h2><div className="finding-box"><p className="finding-title">Temperature exceeded the configured limit</p><p>Possible explanation: {finding?.explanation} Evidence supports the hypothesis but does not establish disposition.</p></div><div className="next-checks"><h3>Next checks</h3><ul>{report.next_checks.map(check => <li key={check.code}>{check.reason}</li>)}</ul></div></article>
      <article className="card evidence"><h2>Supporting &amp; conflicting evidence</h2><div className="evidence-list">{evidence.map(([title, detail, status, tone]) => <button key={title} className="evidence-item"><strong>{title}</strong><span>{detail}</span><em className={tone}>{status}</em></button>)}</div></article>
    </section>
    <section className="review-grid"><article className="card review"><div className="review-heading"><div><h2>Operator review</h2><p>Add a note and acknowledge or request more evidence.</p></div><div className="actions"><button className="btn btn-primary">Acknowledge review</button><button className="btn btn-secondary">Request evidence</button></div></div><textarea maxLength={1000} placeholder="Add review note…" aria-label="Review note" /></article><article className="card report"><div className="report-heading"><div><h2>Report ready</h2><p>Evidence references and measurements checked</p></div><button className="btn btn-primary">Download</button></div><small>IDs, request IDs and raw service names live in expandable diagnostics.</small></article></section>
    <p className="footer-note">Run {demoRun.run_id} · Last updated {formatTime(demoRun.completed_at!)} UTC</p>
  </div></main>;
}
