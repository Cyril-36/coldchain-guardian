import { useState } from "react";
import { demoReport, demoRun, demoSnapshot } from "./mockData";
import { TelemetryChart } from "./TelemetryChart";
import { PrintableReport } from "./PrintableReport";
import { useRunData } from "./useRunData";
import { useOperatorActions } from "./useOperatorActions";
import { navigation } from "./navigation";
import { useAuth } from "../auth/AuthProvider";
import type { Report, Snapshot, ReviewDecision, Review } from "../types/contracts";

const stageLabels: Record<string, string> = {
  preparing: "Preparing",
  detecting: "Detecting",
  collecting_evidence: "Collecting evidence",
  comparing_hypotheses: "Comparing hypotheses",
  verifying: "Verifying",
  ready: "Ready",
  failed: "Failed",
};

type FixtureEvidenceItem = { key: string; title: string; detail: string; tone: "green" | "amber"; recordIds: string[] };

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(value));
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds <= 0) return "0s";

  const total = Number(seconds.toFixed(1));
  const formatSecs = (val: number) => (Number.isInteger(val) ? `${val}` : `${val.toFixed(1)}`);

  if (total < 60) {
    return `${formatSecs(total)}s`;
  }

  const mins = Math.floor(total / 60);
  const remSecs = Number((total - mins * 60).toFixed(1));

  if (remSecs === 60) {
    return `${mins + 1} min`;
  }
  if (remSecs === 0) {
    return `${mins} min`;
  }
  return `${mins} min ${formatSecs(remSecs)}s`;
}

function MetricCard({ label, value, detail, tone }: { label: string; value: string; detail: string; tone: "critical" | "review" | "info" }) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-detail">
        {detail}
      </p>
    </article>
  );
}

function outcomeCopy(report: Report) {
  if (report.outcome === "no_excursion") {
    return {
      title: "No excursion detected",
      detail: "The deterministic measurements did not report an out-of-range excursion.",
    };
  }
  if (report.outcome === "unresolved") {
    return {
      title: "Investigation remains unresolved",
      detail: "Available evidence is insufficient to support a primary explanation.",
    };
  }
  const finding = report.hypotheses.find((item) => item.hypothesis === report.primary_hypothesis);
  return {
    title: "Temperature exceeded the configured limit",
    detail: finding?.explanation ?? "A supported explanation is recorded in the report.",
  };
}

function fixtureEvidence(snapshot: Snapshot): FixtureEvidenceItem[] {
  const door = snapshot.events.find((event) => event.event_type === "door_state");
  const gap = snapshot.events.find((event) => event.value.includes("telemetry gap"));
  const reference = snapshot.sensors.find((sensor) => sensor.role === "reference");
  const comparison = snapshot.sensors.find((sensor) => sensor.role === "comparison");
  const peak = snapshot.readings.find((reading) => reading.sensor_id === reference?.sensor_id && reading.temperature_c > snapshot.policy.max_c);
  const comparisonReading = snapshot.readings.find((reading) => reading.sensor_id === comparison?.sensor_id);

  return [
    door && { key: door.event_id, title: "Door event", detail: `${formatTime(door.observed_at)} UTC · ${door.value}`, tone: "green" as const, recordIds: [door.event_id] },
    peak && { key: peak.event_id, title: "Temperature peak", detail: `${peak.temperature_c}°C · Sensor A`, tone: "green" as const, recordIds: [peak.event_id] },
    comparisonReading && { key: comparisonReading.event_id, title: "Sensor B", detail: `${comparisonReading.temperature_c}°C · comparison`, tone: "amber" as const, recordIds: [comparisonReading.event_id] },
    gap && { key: gap.event_id, title: "Data gap", detail: `${formatTime(gap.observed_at)} UTC · ${gap.value}`, tone: "amber" as const, recordIds: [gap.event_id] },
  ].filter((item): item is FixtureEvidenceItem => Boolean(item));
}

export function DashboardPage() {
  const auth = useAuth();
  const live = useRunData();
  const runId = live.source === "api" ? live.run.run_id : null;
  const actions = useOperatorActions(runId);
  const [selectedRecordIds, setSelectedRecordIds] = useState<Set<string>>(new Set());
  const [reviewNote, setReviewNote] = useState("");
  const [reviewDecision, setReviewDecision] = useState<ReviewDecision | null>(null);
  const [fixtureReview, setFixtureReview] = useState<Review | null>(null);

  if (live.protectedRun && auth.loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-950">
        <section className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">ColdChain Guardian</p>
          <h1 className="mt-3 text-xl font-semibold">Checking operator session</h1>
          <p className="mt-2 text-sm text-slate-500">Completing secure sign-in…</p>
        </section>
      </main>
    );
  }

  if (live.protectedRun && !auth.authenticated) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-slate-50 px-6 text-slate-950">
        <section className="w-full max-w-md rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">ColdChain Guardian</p>
          <h1 className="mt-3 text-2xl font-semibold">Operator sign-in required</h1>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            This investigation URL is protected. Sign in with the configured Cognito operator account to view the run and its evidence.
          </p>
          {auth.error && (
            <div role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs font-medium text-red-800">
              {auth.error.message}
            </div>
          )}
          <button
            type="button"
            onClick={() => void auth.signIn()}
            className="mt-6 w-full rounded-md bg-[#123B5D] px-4 py-3 text-sm font-semibold text-white hover:bg-[#0F304C]"
          >
            Sign in with Cognito
          </button>
          <p className="mt-4 text-center text-[11px] text-slate-400">
            Public demo mode does not require an operator session.
          </p>
        </section>
      </main>
    );
  }

  if (live.source === "api" && !live.snapshotReady) {
    return (
      <main className="min-h-screen bg-slate-50 text-slate-950">
        <div className="mx-auto max-w-[900px] p-5 lg:p-10">
          <section className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
            <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700">Live investigation</p>
            <h1 className="mt-2 text-2xl font-semibold">
              {live.run.status === "failed" ? "Investigation failed" : "Investigation in progress"}
            </h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              Run {live.run.run_id} is at the <span className="font-semibold">{stageLabels[live.run.stage] ?? live.run.stage}</span> stage.
            </p>
            {live.error && (
              <div role="alert" className="mt-6 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-xs font-medium text-red-800">
                {live.error.message}
              </div>
            )}
            <button type="button" onClick={live.retry} className="mt-6 rounded-md border border-slate-200 px-3 py-2 text-xs font-semibold text-[#123B5D]">
              Retry
            </button>
          </section>
        </div>
      </main>
    );
  }

  const run = live.source === "api" ? live.run : (live.run ?? demoRun);
  const snapshot = live.source === "api" ? live.snapshot : (live.snapshot ?? demoSnapshot);
  const report = live.source === "api" ? live.report : (live.report ?? demoReport);
  const isPublicDemo = live.source === "api" && !live.protectedRun;
  const isProtectedRun = live.source === "api" && live.protectedRun;
  const isReportReady = live.source === "api"
    ? (live.run.status === "completed" || live.run.status === "needs_review") && live.snapshotReady && live.reportReady
    : true;
  const referenceSensor = snapshot.sensors.find((sensor) => sensor.role === "reference");
  const primaryMeasurement = report.measurements.find((m) => m.sensor_id === referenceSensor?.sensor_id) ?? report.measurements[0];
  const refReadings = snapshot.readings.filter((r) => r.sensor_id === referenceSensor?.sensor_id);
  const snapshotPeakC = refReadings.length > 0
    ? Math.max(...refReadings.map((r) => r.temperature_c))
    : (snapshot.readings.length > 0 ? Math.max(...snapshot.readings.map((r) => r.temperature_c)) : null);
  const observedPeak = isReportReady ? (primaryMeasurement?.observed_max_c ?? snapshotPeakC) : snapshotPeakC;
  const copy = outcomeCopy(report);
  const finding = report.hypotheses.find((item) => item.hypothesis === report.primary_hypothesis);
  const unresolvedFinding = report.hypotheses.find((item) => item.assessment === "insufficient");
  const evidence = live.source === "api"
    ? report.evidence
    : (report.evidence.length > 0 ? report.evidence : fixtureEvidence(snapshot));
  const currentReview = actions.review ?? fixtureReview ?? run.review;
  const verificationCopy = report.verification.status === "passed"
    ? "Evidence references and measurements checked"
    : "Verification blocked";

  const handleRun = async () => {
    try {
      if (!auth.authenticated) {
        await auth.signIn();
        return;
      }
      await actions.createRun();
    } catch {}
  };

  const handleReview = async (decision: ReviewDecision) => {
    if (!reviewNote.trim() || actions.reviewSubmitting || Boolean(currentReview)) return;
    setReviewDecision(null);
    if (live.source === "fixture") {
      const mockRev: Review = {
        review_id: "00000000-0000-0000-0000-000000009999",
        run_id: run.run_id,
        report_id: report.report_id,
        decision,
        note: reviewNote.trim(),
        actor_sub: "operator-session-fixture",
        reviewed_at: new Date().toISOString(),
      };
      setFixtureReview(mockRev);
      setReviewDecision(decision);
      return;
    }
    if (!isProtectedRun) return;
    try {
      await actions.submitReview({ decision, note: reviewNote.trim(), report_id: report.report_id });
      setReviewDecision(decision);
    } catch {}
  };

  const handleDownload = async () => {
    if (!isPublicDemo && !isProtectedRun) return;
    try {
      await actions.downloadReport(isPublicDemo);
    } catch {}
  };

  return (
    <main className="app-shell min-h-screen text-slate-950">
      <div className="app-container mx-auto max-w-[1440px] p-5 lg:p-8">
        {live.source === "fixture" && (
          <div role="status" className="dashboard-chrome mb-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-800">
            Demo fixture mode — no live backend data is being shown.
          </div>
        )}
        {live.error && (
          <div role="alert" aria-live="assertive" className="dashboard-chrome mb-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-800">
            {live.error.message}
          </div>
        )}
        {actions.error && (
          <div role="alert" aria-live="assertive" className="dashboard-chrome mb-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-800">
            {actions.error.message}
          </div>
        )}
        {auth.error && (
          <div role="alert" aria-live="assertive" className="dashboard-chrome mb-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-800">
            {auth.error.message}
          </div>
        )}

        <header className="dashboard-chrome hero-header">
          <div className="brand-lockup">
            <div className="brand-title"><span className="brand-mark" aria-hidden="true">CG</span><h1>ColdChain Guardian</h1></div>
            <p>Shipment intelligence &amp; investigation workspace</p>
          </div>
          <label className="case-control"><span>Demo case</span>
            <select
              aria-label="Demo case"
              value={live.selectedDemoId ?? live.demoRuns[0]?.run_id ?? "door"}
              onChange={(e) => {
                const val = e.target.value;
                if (live.source === "api") {
                  navigation.assign(`${window.location.pathname}?demo_run_id=${encodeURIComponent(val)}`);
                } else {
                  navigation.assign(`${window.location.pathname}?case=${encodeURIComponent(val)}`);
                }
              }}
              className="case-select"
            >
              {live.demoRuns.map((demo) => (
                <option key={demo.run_id} value={demo.run_id}>
                  {demo.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={() => void handleRun()}
            disabled={auth.loading || actions.runCreating}
            className="btn btn-primary"
          >
            {auth.loading ? "Checking session…" : actions.runCreating ? "Submitting run…" : "Run investigation"}
          </button>
          <button
            type="button"
            onClick={() => window.print()}
            disabled={!isReportReady}
            className="btn btn-secondary"
            aria-label="Print investigation report"
          >
            Print report
          </button>
        </header>

        <div className="dashboard-chrome run-context">
          <span><strong>Shipment</strong> {run.shipment_id.slice(-8)}</span>
          <span><strong>Policy</strong> {snapshot.policy.policy_id}-{snapshot.policy.policy_version} · {snapshot.policy.min_c}°C–{snapshot.policy.max_c}°C</span>
          <span><strong>Cutoff</strong> {new Date(snapshot.cutoff_at).toLocaleDateString("en-GB")} · {formatTime(snapshot.cutoff_at)} UTC</span>
          <span className="context-pill context-pill--simulated">SIMULATED</span>
          {isReportReady && report.generation_mode === "deterministic_only" && (
            <span className="context-pill context-pill--deterministic">DETERMINISTIC ONLY</span>
          )}
          {isReportReady && report.generation_mode === "bedrock" && (
            <span className="context-pill context-pill--agent">BEDROCK AGENT</span>
          )}
        </div>

        <div className="dashboard-content">
          <section aria-label="Investigation summary" className="metric-grid">
            <MetricCard
              label="Observed peak"
              value={observedPeak == null ? "—" : `${observedPeak}°C`}
              detail={observedPeak != null && observedPeak > snapshot.policy.max_c ? "Above configured limit" : "Within configured limit"}
              tone={observedPeak != null && observedPeak > snapshot.policy.max_c ? "critical" : "info"}
            />
            <MetricCard
              label="Estimated time out of range"
              value={isReportReady ? formatDuration(primaryMeasurement?.estimated_out_of_range_seconds ?? null) : "Analyzing…"}
              detail={
                !isReportReady
                  ? "Detector running"
                  : primaryMeasurement?.censored_start || primaryMeasurement?.censored_end
                  ? "Incomplete exposure · boundary censored"
                  : primaryMeasurement?.coverage_status === "partial"
                  ? "Qualified estimate · partial coverage"
                  : "Backend measurement"
              }
              tone={!isReportReady ? "info" : (primaryMeasurement?.censored_start || primaryMeasurement?.censored_end ? "critical" : "review")}
            />
            <MetricCard
              label="Data coverage"
              value={isReportReady ? (primaryMeasurement?.coverage_status ? primaryMeasurement.coverage_status.toUpperCase() : "—") : (snapshot.readings.length > 0 ? `${snapshot.readings.length} SAMPLES` : "—")}
              detail={!isReportReady ? "Telemetry loaded · analysis in progress" : (primaryMeasurement?.unknown_duration_seconds ? `${formatDuration(primaryMeasurement.unknown_duration_seconds)} unknown duration` : "Full coverage observed")}
              tone={!isReportReady ? "info" : (primaryMeasurement?.coverage_status === "insufficient" ? "critical" : primaryMeasurement?.coverage_status === "partial" ? "review" : "info")}
            />
            <MetricCard
              label="Review status"
              value={!isReportReady ? "In progress" : (currentReview ? `Reviewed · ${currentReview.decision.replace("_", " ")}` : report.review_required ? "Needs review" : "No review required")}
              detail={!isReportReady ? (stageLabels[run.stage] ?? run.stage) : verificationCopy}
              tone={!isReportReady ? "info" : (report.review_required && !currentReview ? "review" : "info")}
            />
          </section>

          <section className="telemetry-section mt-4">
            <TelemetryChart snapshot={snapshot} highlightedRecordIds={selectedRecordIds} onRecordSelect={(ids) => setSelectedRecordIds(new Set(ids))} />
          </section>

          <section className="insight-grid mt-4">
            <article className="panel-card">
              <div className="panel-head"><div><span className="eyebrow">WORKFLOW</span><h2>Investigation stages</h2></div><span className="mini-status">{stageLabels[run.stage] ?? run.stage}</span></div>
              <div className="mt-5 space-y-3">
                {run.stage_events.length > 0 ? (
                  run.stage_events.map((stage) => (
                    <div key={stage.event_id} className="flex items-center gap-3">
                      <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${stage.status === "failed" ? "bg-red-700" : stage.status === "completed" ? "bg-green-700" : "bg-amber-600"}`} />
                      <span className="flex-1 text-xs font-semibold">{stageLabels[stage.stage]}</span>
                      <span className="text-[11px] text-slate-500">{stage.status}</span>
                    </div>
                  ))
                ) : (
                  <span className="text-xs">{stageLabels[run.stage]}</span>
                )}
              </div>
            </article>

            <article className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm">
              <div className="panel-head"><div><span className="eyebrow">INVESTIGATION RESULT</span><h2>{!isReportReady ? "Investigation in progress" : "Main finding"}</h2></div>{isReportReady && <span className={`mini-status ${report.outcome === "unresolved" ? "mini-status--warning" : "mini-status--success"}`}>{report.outcome === "unresolved" ? "UNRESOLVED" : "HYPOTHESIS SUPPORTED"}</span>}</div>
              {!isReportReady ? (
                <div className="mt-4">
                  <div className="flex items-center gap-2">
                    <span className="rounded bg-cyan-50 px-2 py-0.5 text-xs font-semibold text-cyan-800">
                      {stageLabels[run.stage] ?? run.stage}
                    </span>
                    <span className="text-xs text-slate-500">Run {run.run_id}</span>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-slate-600">
                    Run {run.run_id} is at the <span className="font-semibold">{stageLabels[run.stage] ?? run.stage}</span> stage. Telemetry snapshot has loaded. Automated excursion detection and hypothesis evaluation are underway.
                  </p>
                  <div className="mt-4 flex items-center gap-3">
                    <button
                      type="button"
                      onClick={live.retry}
                      className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-semibold text-[#123B5D] hover:bg-slate-100"
                    >
                      Retry
                    </button>
                    <span className="text-[11px] text-slate-400">Polling automatically every few seconds</span>
                  </div>
                </div>
              ) : (
                <>
                  <p className={`finding-title ${report.outcome === "no_excursion" ? "finding-title--normal" : report.outcome === "unresolved" ? "finding-title--warning" : ""}`}>
                    {copy.title}
                  </p>
                  <p className="finding-detail">{copy.detail}</p>
                  {report.outcome === "unresolved" && (
                    <div role="alert" className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
                      <p className="font-semibold">⚠️ Prominent Uncertainty</p>
                      <p className="mt-1">Available evidence cannot confirm a primary cause. Do not release shipment based on incomplete evidence.</p>
                      {unresolvedFinding?.missing_evidence?.length ? (
                        <div className="mt-2">
                          <p className="font-semibold text-[11px] text-amber-800">Missing evidence:</p>
                          <ul className="mt-1 list-disc pl-4 space-y-1 text-[11px]">
                            {unresolvedFinding.missing_evidence.map((item, idx) => (
                              <li key={idx}>{item}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                  )}
                  {report.outcome === "no_excursion" && (
                    <p className="mt-2 text-[11px] text-slate-500">
                      Normal readings do not certify product viability or shipment release.
                    </p>
                  )}
                  {finding && report.outcome !== "unresolved" && (
                    <p className="mt-2 text-[11px] text-amber-700">Evidence supports the hypothesis but does not establish disposition.</p>
                  )}
                  {report.next_checks.length > 0 && (
                    <>
                      <h3 className="mt-4 text-xs font-semibold text-slate-600">Next checks</h3>
                      <ul className="mt-2 space-y-2 text-xs">
                        {report.next_checks.map((check) => (
                          <li key={check.code}>• {check.reason}</li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
            </article>

            <article className="rounded-[10px] border border-slate-200 bg-white p-5 shadow-sm">
              <div className="panel-head"><div><span className="eyebrow">EVIDENCE TRAIL</span><h2>Supporting &amp; conflicting evidence</h2></div><span className="mini-status">{evidence.length} citations</span></div>
              <div className="mt-4 space-y-2">
                {!isReportReady ? (
                  <p className="text-xs text-slate-500">Evidence citations will appear once analysis completes.</p>
                ) : evidence.length === 0 ? (
                  <p className="text-xs text-slate-500">No cited evidence for this report.</p>
                ) : (
                  evidence.map((item) => {
                    const ids = "evidence_id" in item ? item.record_ids : item.recordIds;
                    const active = ids.some((id) => selectedRecordIds.has(id));
                    const title = "evidence_id" in item ? item.summary : item.title;
                    return (
                      <button
                        key={"evidence_id" in item ? item.evidence_id : item.key}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setSelectedRecordIds(active ? new Set() : new Set(ids))}
                        className={`evidence-item ${active ? "evidence-item--active" : ""}`}
                      >
                        <span className="evidence-copy"><span>{title}</span><small>{"evidence_id" in item ? `${item.record_ids.length} record${item.record_ids.length === 1 ? "" : "s"}` : "View on timeline"}</small></span><span className="evidence-arrow" aria-hidden="true">↗</span>
                      </button>
                    );
                  })
                )}
              </div>
            </article>
          </section>

          <section className="dashboard-chrome review-grid mt-4">
            <article className="review-card">
              <div className="panel-head"><div><span className="eyebrow">HUMAN REVIEW</span><h2>Operator review</h2></div><span className="mini-status mini-status--warning">REQUIRED</span></div>
              <p className="mt-1 text-[11px] text-slate-500">
                Review acknowledgement records operator review only and does not constitute shipment release or professional QA approval.
              </p>
              {!isReportReady ? (
                <p className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
                  Operator review is enabled once the investigation report is complete.
                </p>
              ) : (
                <>
                  {reviewDecision && <p role="status" className="mt-2 text-[11px] text-green-700">Review saved.</p>}
                  {currentReview ? (
                    <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-slate-950">Recorded Review · {currentReview.decision.replace("_", " ")}</span>
                        <span className="text-[11px] text-slate-500">{new Date(currentReview.reviewed_at).toUTCString()}</span>
                      </div>
                      <p className="mt-1 text-slate-600">
                        <strong>Reviewer Identity:</strong> <span data-testid="reviewer-identity">{currentReview.actor_sub}</span>
                      </p>
                      {currentReview.note && <p className="mt-1 text-slate-700 italic">"{currentReview.note}"</p>}
                    </div>
                  ) : (
                    <>
                      <div className="mt-3 flex gap-2">
                        <button
                          type="button"
                          disabled={(!isProtectedRun && live.source !== "fixture") || !reviewNote.trim() || actions.reviewSubmitting}
                          onClick={() => void handleReview("acknowledged")}
                          className="btn btn-primary btn-small disabled:opacity-50"
                        >
                          Acknowledge review
                        </button>
                        <button
                          type="button"
                          disabled={(!isProtectedRun && live.source !== "fixture") || !reviewNote.trim() || actions.reviewSubmitting}
                          onClick={() => void handleReview("request_more_evidence")}
                          className="btn btn-secondary btn-small disabled:opacity-50"
                        >
                          Request evidence
                        </button>
                      </div>
                      <textarea
                        value={reviewNote}
                        onChange={(event) => setReviewNote(event.target.value)}
                        maxLength={1000}
                        placeholder="Add review note…"
                        aria-label="Review note"
                        className="review-textarea mt-3"
                      />
                      <p className="mt-1 text-right text-[10px] text-slate-400" aria-live="polite">
                        {reviewNote.length}/1000
                      </p>
                    </>
                  )}
                </>
              )}
            </article>

            <article className="report-card">
              <div className="panel-head">
                <div>
                  <h2 className="text-base font-semibold">{isReportReady ? "Report ready" : "Report pending"}</h2>
                  <p className={`text-[10px] ${!isReportReady ? "text-slate-500" : (report.verification.status === "passed" ? "text-green-700" : "text-amber-700")}`}>
                    {!isReportReady ? "Awaiting report generation" : verificationCopy}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={(!isPublicDemo && !isProtectedRun) || !isReportReady}
                  onClick={() => void handleDownload()}
                  className="rounded-md bg-[#123B5D] px-3 py-2 text-[11px] font-semibold text-white disabled:opacity-50"
                >
                  Download
                </button>
              </div>
              {isReportReady && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-[10px] font-semibold">Diagnostics</summary>
                  <p className="mt-2 text-[10px] text-slate-500">Run ID: {run.run_id}</p>
                  <p className="text-[10px] text-slate-500">Report ID: {report.report_id}</p>
                  <p className="text-[10px] text-slate-500">Snapshot SHA-256: {report.snapshot_sha256}</p>
                  <p className="text-[10px] text-slate-500">Generation: {report.generation_mode}</p>
                </details>
              )}
            </article>
          </section>
        </div>

        {isReportReady && (
          <PrintableReport run={run} snapshot={snapshot} report={report} reviewOverride={currentReview} />
        )}
      </div>
    </main>
  );
}

