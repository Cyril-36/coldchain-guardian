import type { Report, Run, Snapshot } from "../types/contracts";

function utc(value: string | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(value)) + " UTC";
}

function duration(seconds: number | null | undefined) {
  return seconds == null ? "—" : `${Math.floor(seconds / 60)} min`;
}

export function PrintableReport({ run, snapshot, report }: { run: Run; snapshot: Snapshot; report: Report }) {
  const measurement = report.measurements[0];
  const primary = report.hypotheses.find((item) => item.hypothesis === report.primary_hypothesis);
  return <section className="print-report" aria-labelledby="print-report-title">
    <header className="print-report-header"><div><p className="print-kicker">ColdChain Guardian</p><h1 id="print-report-title">Cold-chain investigation report</h1><p>Evidence-backed investigation record · simulated shipment</p></div><div className="print-meta"><strong>Run</strong><span>{run.run_id}</span><strong>Report</strong><span>{report.report_id}</span></div></header>
    <div className="print-grid"><div><strong>Shipment</strong><span>{run.shipment_id}</span></div><div><strong>Policy</strong><span>{snapshot.policy.policy_id} · {snapshot.policy.policy_version}</span></div><div><strong>Configured range</strong><span>{snapshot.policy.min_c}°C–{snapshot.policy.max_c}°C</span></div><div><strong>Cutoff</strong><span>{utc(snapshot.cutoff_at)}</span></div></div>
    <section className="print-section"><h2>Measurements</h2><div className="print-grid"><div><strong>Observed maximum</strong><span>{measurement?.observed_max_c == null ? "—" : `${measurement.observed_max_c}°C`}</span></div><div><strong>Estimated time out of range</strong><span>{duration(measurement?.estimated_out_of_range_seconds)}</span></div><div><strong>Coverage</strong><span>{measurement?.coverage_status ?? "—"}</span></div><div><strong>Unknown duration</strong><span>{duration(measurement?.unknown_duration_seconds)}</span></div></div></section>
    <section className="print-section"><h2>Investigation outcome</h2><p><strong>{report.outcome.replaceAll("_", " ")}</strong></p>{primary && <p>{primary.explanation}</p>}</section>
    <section className="print-section"><h2>Evidence</h2>{report.evidence.length ? <ul>{report.evidence.map((item) => <li key={item.evidence_id}><strong>{item.kind.replaceAll("_", " ")}</strong> — {item.summary}{item.record_ids.length ? ` · Records: ${item.record_ids.join(", ")}` : ""}</li>)}</ul> : <p>No evidence references were returned.</p>}</section>
    <section className="print-section"><h2>Next checks</h2><ul>{report.next_checks.map((check) => <li key={check.code}>{check.reason}</li>)}</ul></section>
    <section className="print-section"><h2>Limitations & verification</h2><ul>{report.limitations.map((item) => <li key={item}>{item}</li>)}</ul><p><strong>Verification:</strong> {report.verification.status === "passed" ? "Evidence references and measurements checked." : "Verification blocked."}</p><p><strong>Generation mode:</strong> {report.generation_mode}</p></section>
    <footer className="print-footer"><span>Generated from the backend investigation artifact.</span><span>Simulated data — not a physical sensor record.</span></footer>
  </section>;
}
