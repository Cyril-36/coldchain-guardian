import { useState } from "react";
import { demoReport, demoRun, demoSnapshot } from "./mockData";
import { TelemetryChart } from "./TelemetryChart";
import { PrintableReport } from "./PrintableReport";
import { useRunData } from "./useRunData";
import { useOperatorActions } from "./useOperatorActions";
import { navigation } from "./navigation";
import { useAuth } from "../auth/AuthProvider";
import type {
  Report,
  Snapshot,
  ReviewDecision,
  Review,
  RunStage,
  RunStatus,
  StageEvent,
} from "../types/contracts";

const stageLabels: Record<string, string> = {
  preparing: "Preparing",
  detecting: "Detecting",
  collecting_evidence: "Collecting evidence",
  comparing_hypotheses: "Comparing hypotheses",
  verifying: "Verifying",
  ready: "Ready",
  failed: "Failed",
};

const PIPELINE_STAGES: Array<{ id: RunStage; label: string; description: string }> = [
  { id: "detecting", label: "Detection", description: "Sensor range & threshold check" },
  { id: "collecting_evidence", label: "Evidence collection", description: "Events, readings & data gaps" },
  { id: "comparing_hypotheses", label: "Hypothesis comparison", description: "Root-cause evaluation" },
  { id: "verifying", label: "Verification", description: "Provenance & constraint checks" },
  { id: "ready", label: "Complete / review", description: "Report generated · operator review" },
];

type FixtureEvidenceItem = {
  key: string;
  title: string;
  detail: string;
  tone: "green" | "amber";
  recordIds: string[];
  kind?: string;
  observedAt?: string;
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(new Date(value));
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

function MetricCard({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: "critical" | "review" | "info";
}) {
  return (
    <article className={`metric-card metric-card--${tone}`}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-detail">{detail}</div>
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
  const peak = snapshot.readings.find(
    (reading) => reading.sensor_id === reference?.sensor_id && reading.temperature_c > snapshot.policy.max_c,
  );
  const comparisonReading = snapshot.readings.find((reading) => reading.sensor_id === comparison?.sensor_id);

  const items: Array<FixtureEvidenceItem | null> = [
    door
      ? {
          key: door.event_id,
          title: "Door event",
          detail: `${formatTime(door.observed_at)} UTC · ${door.value}`,
          tone: "green",
          recordIds: [door.event_id],
          kind: "DOOR EVENT",
          observedAt: door.observed_at,
        }
      : null,
    peak
      ? {
          key: peak.event_id,
          title: "Temperature peak",
          detail: `${peak.temperature_c}°C · Sensor A`,
          tone: "green",
          recordIds: [peak.event_id],
          kind: "SENSOR READING",
          observedAt: peak.observed_at,
        }
      : null,
    comparisonReading
      ? {
          key: comparisonReading.event_id,
          title: "Sensor B",
          detail: `${comparisonReading.temperature_c}°C · comparison`,
          tone: "amber",
          recordIds: [comparisonReading.event_id],
          kind: "SENSOR READING",
          observedAt: comparisonReading.observed_at,
        }
      : null,
    gap
      ? {
          key: gap.event_id,
          title: "Data gap",
          detail: `${formatTime(gap.observed_at)} UTC · ${gap.value}`,
          tone: "amber",
          recordIds: [gap.event_id],
          kind: "DATA GAP",
          observedAt: gap.observed_at,
        }
      : null,
  ];

  return items.filter((item): item is FixtureEvidenceItem => item !== null);
}

function getStageState(
  stageId: RunStage,
  currentStage: RunStage,
  runStatus: RunStatus,
  stageEvents: StageEvent[],
): { status: "completed" | "running" | "failed" | "pending"; detail?: string } {
  // 1. Check explicit stage events from backend
  const event = stageEvents.find((e) => e.stage === stageId);
  if (event) {
    if (event.status === "completed") {
      return { status: "completed", detail: event.tool_name ? `Tool: ${event.tool_name}` : "Completed" };
    }
    if (event.status === "failed") {
      return { status: "failed", detail: "Stage failed" };
    }
    if (event.status === "started") {
      return { status: "running", detail: event.tool_name ? `Running ${event.tool_name}` : "In progress" };
    }
  }

  // 2. Derive state based on pipeline progression
  const stageOrder: RunStage[] = [
    "preparing",
    "detecting",
    "collecting_evidence",
    "comparing_hypotheses",
    "verifying",
    "ready",
  ];
  const targetIdx = stageOrder.indexOf(stageId);
  const currentIdx = stageOrder.indexOf(currentStage);

  if (runStatus === "failed") {
    if (targetIdx < currentIdx) return { status: "completed", detail: "Completed" };
    if (targetIdx === currentIdx) return { status: "failed", detail: "Failed" };
    return { status: "pending", detail: "Pending" };
  }

  if (currentStage === "ready" || runStatus === "completed" || runStatus === "needs_review") {
    return { status: "completed", detail: "Completed" };
  }

  if (targetIdx < currentIdx) {
    return { status: "completed", detail: "Completed" };
  }
  if (targetIdx === currentIdx) {
    return { status: "running", detail: "In progress" };
  }
  return { status: "pending", detail: "Pending" };
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

  const handleRun = async () => {
    try {
      if (!auth.authenticated) {
        await auth.signIn();
        return;
      }
      await actions.createRun();
    } catch {}
  };

  // A successful but empty public demo list. Rendered before the loading/error
  // skeleton, which would otherwise leave the first operator with no way in: no
  // sign-in, no run creation, just a spinner. No fixture data and no invented run id.
  if (live.publicDemosEmpty) {
    return (
      <main className="min-h-screen bg-slate-50 text-slate-950 flex items-center justify-center p-6">
        <div className="w-full max-w-[640px]">
          <section className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
            <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">
              ColdChain Guardian
            </p>
            <h1 className="mt-2 text-2xl font-bold tracking-tight">No public demos yet</h1>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              No investigations have been published for public viewing. An operator can
              sign in and run the first investigation against simulated cold-chain
              telemetry; completed runs can then be published here.
            </p>
            <button
              type="button"
              onClick={() => void handleRun()}
              disabled={auth.loading || actions.runCreating}
              className="mt-6 inline-flex items-center justify-center rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold text-white hover:bg-cyan-800 disabled:opacity-60"
            >
              {auth.loading
                ? "Checking session…"
                : actions.runCreating
                  ? "Submitting run…"
                  : auth.authenticated
                    ? "Run investigation"
                    : "Sign in to start investigation"}
            </button>
            {actions.error && (
              <p className="mt-3 text-sm text-rose-700">{actions.error.message}</p>
            )}
          </section>
        </div>
      </main>
    );
  }

  if (live.source === "api" && !live.snapshotReady) {
    return (
      <main className="min-h-screen bg-slate-50 text-slate-950 flex items-center justify-center p-6">
        <div className="w-full max-w-[640px]">
          <section className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-cyan-600 animate-pulse" aria-hidden="true" />
              <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">Live investigation</p>
            </div>
            <h1 className="mt-2 text-2xl font-bold tracking-tight">
              {!live.runReady
                ? "Loading investigation"
                : live.run.status === "failed"
                  ? "Investigation failed"
                  : "Investigation in progress"}
            </h1>
            {/* Until runReady, `live.run` is the fixture placeholder that seeds initial
                state. Printing its id or stage here would show a fabricated run on a
                production page, so the copy stays generic until real data arrives. */}
            {live.runReady ? (
              <p className="mt-2 text-sm leading-6 text-slate-600">
                Run {live.run.run_id} is at the <span className="font-semibold text-slate-900">{stageLabels[live.run.stage] ?? live.run.stage}</span> stage.
              </p>
            ) : (
              <p className="mt-2 text-sm leading-6 text-slate-600">Loading investigation data…</p>
            )}

            {/* Skeleton loading preview */}
            <div className="mt-6 space-y-3 rounded-xl border border-slate-100 bg-slate-50/70 p-4" aria-hidden="true">
              <div className="flex items-center justify-between">
                <div className="h-3 w-32 rounded bg-slate-200 animate-pulse" />
                <div className="h-3 w-16 rounded bg-slate-200 animate-pulse" />
              </div>
              <div className="h-20 w-full rounded-lg bg-slate-200/70 animate-pulse" />
              <div className="flex gap-2">
                <div className="h-2.5 w-24 rounded bg-slate-200 animate-pulse" />
                <div className="h-2.5 w-20 rounded bg-slate-200 animate-pulse" />
              </div>
            </div>

            {live.error && (
              <div role="alert" className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-xs font-medium text-red-800">
                <p className="font-bold">Request Error</p>
                <p className="mt-1">{live.error.message}</p>
              </div>
            )}

            <div className="mt-6 flex items-center gap-3">
              <button
                type="button"
                onClick={live.retry}
                className="btn btn-secondary btn-small"
              >
                Retry
              </button>
              <span className="text-xs text-slate-400">Loading snapshot telemetry…</span>
            </div>
          </section>
        </div>
      </main>
    );
  }

  const run = live.source === "api" ? live.run : (live.run ?? demoRun);
  const snapshot = live.source === "api" ? live.snapshot : (live.snapshot ?? demoSnapshot);
  const isPublicDemo = live.source === "api" && !live.protectedRun;
  const isProtectedRun = live.source === "api" && live.protectedRun;
  const isReportReady =
    live.source === "api"
      ? (live.run.status === "completed" || live.run.status === "needs_review") &&
        live.snapshotReady &&
        live.reportReady &&
        Boolean(live.report)
      : Boolean(live.report ?? demoReport);
  const report = isReportReady ? (live.source === "api" ? live.report : (live.report ?? demoReport)) : null;
  const referenceSensor = snapshot.sensors.find((sensor) => sensor.role === "reference");
  const primaryMeasurement = report
    ? (report.measurements.find((m) => m.sensor_id === referenceSensor?.sensor_id) ?? report.measurements[0])
    : null;
  const refReadings = snapshot.readings.filter((r) => r.sensor_id === referenceSensor?.sensor_id);
  const snapshotPeakC =
    refReadings.length > 0
      ? Math.max(...refReadings.map((r) => r.temperature_c))
      : (snapshot.readings.length > 0 ? Math.max(...snapshot.readings.map((r) => r.temperature_c)) : null);
  const observedPeak = isReportReady ? (primaryMeasurement?.observed_max_c ?? snapshotPeakC) : snapshotPeakC;
  const copy = report ? outcomeCopy(report) : null;
  const finding = report ? report.hypotheses.find((item) => item.hypothesis === report.primary_hypothesis) : null;
  const unresolvedFinding = report ? report.hypotheses.find((item) => item.assessment === "insufficient") : null;
  const evidence =
    live.source === "api"
      ? (report?.evidence ?? [])
      : (report?.evidence && report.evidence.length > 0 ? report.evidence : fixtureEvidence(snapshot));
  const currentReview = actions.review ?? fixtureReview ?? run.review;
  const verificationCopy =
    report?.verification.status === "passed"
      ? "Evidence references and measurements checked"
      : "Verification blocked";

  // Derive scenario / incident title without hardcoding or inventing
  const selectedDemo = live.demoRuns.find(
    (d) => d.run_id === (live.selectedDemoId ?? (live.source === "fixture" ? live.demoRuns[0]?.run_id : null) ?? run.run_id),
  );
  const rawIncidentTitle =
    (live.source === "fixture" && selectedDemo?.label)
      ? selectedDemo.label.replace(/ · Run \d+/, "").replace(/ demo$/i, "")
      : (report?.primary_hypothesis
      ? `${report.primary_hypothesis.replace(/_/g, " ")} investigation`
      : report?.outcome === "unresolved"
      ? "Unresolved excursion investigation"
      : report?.outcome === "no_excursion"
      ? "Normal control monitoring"
      : "Shipment temperature investigation");
  const incidentTitleFormatted = rawIncidentTitle.toUpperCase().includes("INVESTIGATION")
    ? rawIncidentTitle.toUpperCase()
    : `${rawIncidentTitle.toUpperCase()} INVESTIGATION`;

  // Partition supporting vs conflicting evidence
  const supportingIds = new Set(finding?.supporting_evidence_ids ?? []);
  const conflictingIds = new Set(finding?.conflicting_evidence_ids ?? []);

  const handleReview = async (decision: ReviewDecision) => {
    if (!reviewNote.trim() || actions.reviewSubmitting || Boolean(currentReview) || !report) return;
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
    if ((!isPublicDemo && !isProtectedRun) || !isReportReady || !report) return;
    try {
      await actions.downloadReport(isPublicDemo);
    } catch {}
  };

  return (
    <main className="app-shell min-h-screen text-slate-950">
      <div className="app-container mx-auto max-w-[1440px] p-5 lg:p-8">
        {live.source === "fixture" && (
          <div
            role="status"
            className="dashboard-chrome mb-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-xs font-medium text-amber-800"
          >
            Demo fixture mode — no live backend data is being shown.
          </div>
        )}
        {live.error && (
          <div
            role="alert"
            aria-live="assertive"
            className="dashboard-chrome mb-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-800"
          >
            {live.error.message}
          </div>
        )}
        {actions.error && (
          <div
            role="alert"
            aria-live="assertive"
            className="dashboard-chrome mb-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-800"
          >
            {actions.error.message}
          </div>
        )}
        {auth.error && (
          <div
            role="alert"
            aria-live="assertive"
            className="dashboard-chrome mb-3 rounded-md border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-800"
          >
            {auth.error.message}
          </div>
        )}

        {/* Global Controls & Demo Case Selector */}
        <header className="dashboard-chrome hero-header">
          <div className="brand-lockup">
            <div className="brand-title">
              <span className="brand-mark" aria-hidden="true">CG</span>
              <h1>ColdChain Guardian</h1>
            </div>
            <p>Shipment intelligence &amp; investigation workspace</p>
          </div>
          {!isProtectedRun && live.demoRuns.length > 0 && (
            <label className="case-control">
              <span>Demo case</span>
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
          )}
          <button
            type="button"
            onClick={() => void handleRun()}
            disabled={auth.loading || actions.runCreating}
            className="btn btn-primary"
          >
            {(auth.loading || actions.runCreating) && (
              <svg className="btn-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
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

        {/* 1. Incident Context Header */}
        <section className="incident-banner dashboard-chrome mt-3" aria-label="Incident context">
          <div className="incident-banner__header">
            <div className="incident-banner__title-group">
              <span className="incident-banner__eyebrow">INCIDENT CONTEXT</span>
              <h2 className="incident-banner__title">{incidentTitleFormatted}</h2>
            </div>
            <div className="incident-banner__badge-group">
              <span className={`status-badge status-badge--${run.status}`}>
                {run.status === "needs_review"
                  ? "NEEDS REVIEW"
                  : run.status === "completed"
                  ? "COMPLETED"
                  : run.status === "running"
                  ? "RUNNING"
                  : run.status.toUpperCase().replace(/_/g, " ")}
              </span>
            </div>
          </div>

          <div className="run-context incident-banner__meta">
            <span><strong>Shipment</strong> {run.shipment_id.slice(-8)}</span>
            <span><strong>Run</strong> {run.run_id.slice(-8)}</span>
            <span>
              <strong>Policy</strong> {snapshot.policy.policy_id}-{snapshot.policy.policy_version} · {snapshot.policy.min_c}°C–{snapshot.policy.max_c}°C
            </span>
            <span>
              <strong>Cutoff</strong> {new Date(snapshot.cutoff_at).toLocaleDateString("en-GB")} · {formatTime(snapshot.cutoff_at)} UTC
            </span>
            <span className="context-pill context-pill--simulated">SIMULATED</span>
            {isReportReady && report?.generation_mode === "deterministic_only" && (
              <span className="context-pill context-pill--deterministic">DETERMINISTIC ONLY</span>
            )}
            {isReportReady && report?.generation_mode === "bedrock" && (
              <span className="context-pill context-pill--agent">BEDROCK AGENT</span>
            )}
          </div>
        </section>

        <div className="dashboard-content">
          {/* Key Metrics */}
          <section aria-label="Investigation summary" className="metric-grid mt-4">
            <MetricCard
              label="Observed peak"
              value={observedPeak == null ? "—" : `${observedPeak}°C`}
              detail={
                observedPeak != null && observedPeak > snapshot.policy.max_c
                  ? "Above configured limit"
                  : "Within configured limit"
              }
              tone={observedPeak != null && observedPeak > snapshot.policy.max_c ? "critical" : "info"}
            />
            <MetricCard
              label="Estimated time out of range"
              value={
                isReportReady
                  ? formatDuration(primaryMeasurement?.estimated_out_of_range_seconds ?? null)
                  : "Analyzing…"
              }
              detail={
                !isReportReady
                  ? "Detector running"
                  : primaryMeasurement?.censored_start || primaryMeasurement?.censored_end
                  ? "Incomplete exposure · boundary censored"
                  : primaryMeasurement?.coverage_status === "partial"
                  ? "Qualified estimate · partial coverage"
                  : "Backend measurement"
              }
              tone={
                !isReportReady
                  ? "info"
                  : primaryMeasurement?.censored_start || primaryMeasurement?.censored_end
                  ? "critical"
                  : "review"
              }
            />
            <MetricCard
              label="Data coverage"
              value={
                isReportReady
                  ? primaryMeasurement?.coverage_status
                    ? primaryMeasurement.coverage_status.toUpperCase()
                    : "—"
                  : snapshot.readings.length > 0
                  ? `${snapshot.readings.length} SAMPLES`
                  : "—"
              }
              detail={
                !isReportReady
                  ? "Telemetry loaded · analysis in progress"
                  : primaryMeasurement?.unknown_duration_seconds
                  ? `${formatDuration(primaryMeasurement.unknown_duration_seconds)} unknown duration`
                  : "Full coverage observed"
              }
              tone={
                !isReportReady
                  ? "info"
                  : primaryMeasurement?.coverage_status === "insufficient"
                  ? "critical"
                  : primaryMeasurement?.coverage_status === "partial"
                  ? "review"
                  : "info"
              }
            />
            <MetricCard
              label="Review status"
              value={
                !isReportReady
                  ? "In progress"
                  : currentReview
                  ? `Reviewed · ${currentReview.decision.replace("_", " ")}`
                  : report?.review_required
                  ? "Needs review"
                  : "No review required"
              }
              detail={!isReportReady ? (stageLabels[run.stage] ?? run.stage) : verificationCopy}
              tone={!isReportReady ? "info" : report?.review_required && !currentReview ? "review" : "info"}
            />
          </section>

          {/* 2. Temperature Telemetry Timeline */}
          <section className="telemetry-section mt-4">
            <TelemetryChart
              snapshot={snapshot}
              highlightedRecordIds={selectedRecordIds}
              onRecordSelect={(ids) => setSelectedRecordIds(new Set(ids))}
            />
          </section>

          {/* 3, 4, 5. Investigation Stages, Main Finding, and Evidence */}
          <section className="insight-grid mt-4">
            {/* Panel 1: Investigation Stages Timeline */}
            <article className="panel-card stages-panel">
              <div className="panel-head">
                <div>
                  <span className="eyebrow">WORKFLOW</span>
                  <h2>Investigation stages</h2>
                </div>
                <span className="mini-status">{stageLabels[run.stage] ?? run.stage}</span>
              </div>

              <div className="stage-pipeline mt-4">
                {PIPELINE_STAGES.map((stageItem, index) => {
                  const state = getStageState(stageItem.id, run.stage, run.status, run.stage_events);
                  const isLast = index === PIPELINE_STAGES.length - 1;

                  return (
                    <div
                      key={stageItem.id}
                      className={`stage-step stage-step--${state.status}`}
                    >
                      <div className="stage-step__indicator-col">
                        <div
                          className={`stage-node stage-node--${state.status}`}
                          aria-label={`${stageItem.label}: ${state.status}`}
                        >
                          {state.status === "completed" && <span className="stage-node__icon">✓</span>}
                          {state.status === "running" && <span className="stage-node__pulse" />}
                          {state.status === "failed" && <span className="stage-node__icon">✕</span>}
                          {state.status === "pending" && <span className="stage-node__dot" />}
                        </div>
                        {!isLast && <div className={`stage-connector stage-connector--${state.status}`} />}
                      </div>

                      <div className="stage-step__content">
                        <div className="flex items-center justify-between gap-2">
                          <span className="stage-step__name font-semibold text-xs text-slate-900">
                            {stageItem.label}
                          </span>
                          <span className={`stage-step__badge stage-step__badge--${state.status}`}>
                            {state.status === "completed"
                              ? "Done"
                              : state.status === "running"
                              ? "Active"
                              : state.status === "failed"
                              ? "Failed"
                              : "Pending"}
                          </span>
                        </div>
                        <p className="stage-step__description text-[11px] text-slate-500 mt-0.5">
                          {state.detail ?? stageItem.description}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Show tool events if available */}
              {run.stage_events.length > 0 && (
                <div className="mt-4 border-t border-slate-100 pt-3">
                  <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                    Recorded execution events
                  </span>
                  <div className="mt-2 space-y-1.5">
                    {run.stage_events.map((evt) => (
                      <div key={evt.event_id} className="flex items-center justify-between text-[10px] text-slate-600 bg-slate-50 px-2 py-1 rounded">
                        <span>{stageLabels[evt.stage]}</span>
                        <span className="font-mono text-slate-400">{evt.tool_name ?? evt.status}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </article>

            {/* Panel 2: Main Finding & Supported Hypothesis */}
            <article className="panel-card finding-panel">
              <div className="panel-head">
                <div>
                  <span className="eyebrow">INVESTIGATION RESULT</span>
                  <h2>{!isReportReady ? "Investigation in progress" : "Main finding"}</h2>
                </div>
                {isReportReady && report && (
                  <span
                    className={`mini-status ${
                      report.outcome === "unresolved"
                        ? "mini-status--warning"
                        : report.outcome === "no_excursion"
                        ? "mini-status--neutral"
                        : "mini-status--success"
                    }`}
                  >
                    {report.outcome === "unresolved"
                      ? "UNRESOLVED"
                      : report.outcome === "no_excursion"
                      ? "NO EXCURSION"
                      : "HYPOTHESIS SUPPORTED"}
                  </span>
                )}
              </div>

              {!isReportReady || !report || !copy ? (
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
                <div className="finding-narrative mt-4">
                  {/* Headline */}
                  <div className="finding-headline-box">
                    <p
                      className={`finding-title ${
                        report.outcome === "no_excursion"
                          ? "finding-title--normal"
                          : report.outcome === "unresolved"
                          ? "finding-title--warning"
                          : ""
                      }`}
                    >
                      {copy.title}
                    </p>
                  </div>

                  {/* Supported Hypothesis Card */}
                  {report.outcome !== "no_excursion" && (
                    <div className="hypothesis-block mt-3">
                      <div className="hypothesis-block__header">
                        <span className="hypothesis-block__tag">
                          {report.outcome === "unresolved"
                            ? "EVALUATED HYPOTHESIS"
                            : "SUPPORTED HYPOTHESIS"}
                        </span>
                        {report.primary_hypothesis && (
                          <span className="hypothesis-block__name">
                            {report.primary_hypothesis.replace(/_/g, " ").toUpperCase()}
                          </span>
                        )}
                      </div>
                      <p className="hypothesis-block__detail">{copy.detail}</p>
                    </div>
                  )}

                  {/* Why this matters / investigation context */}
                  <div className="finding-context-box mt-3">
                    <span className="finding-context-box__kicker">WHY THIS MATTERS</span>
                    {finding && report.outcome !== "unresolved" && (
                      <p className="finding-context-box__text">
                        Evidence supports the hypothesis but does not establish disposition.
                      </p>
                    )}
                    {report.outcome === "no_excursion" && (
                      <p className="finding-context-box__text text-slate-500">
                        Normal readings do not certify product viability or shipment release.
                      </p>
                    )}
                    {report.outcome === "unresolved" && (
                      <p className="finding-context-box__text text-amber-900">
                        Available evidence cannot confirm a primary cause. Do not release shipment based on incomplete evidence.
                      </p>
                    )}
                  </div>

                  {/* Deterministic Mode Callout */}
                  {isReportReady && report.generation_mode === "deterministic_only" && (
                    <div className="deterministic-callout mt-3">
                      <div className="flex items-start gap-2">
                        <span className="text-cyan-700 text-xs mt-0.5" aria-hidden="true">ℹ️</span>
                        <div>
                          <p className="text-[10px] font-bold text-slate-800 uppercase tracking-wide">
                            Deterministic analysis
                          </p>
                          <p className="text-[11px] text-slate-600 leading-normal">
                            The investigation completed using deterministic analysis. AI-generated reasoning was unavailable for this run.
                          </p>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Prominent Uncertainty Alert for Unresolved */}
                  {report.outcome === "unresolved" && (
                    <div
                      role="alert"
                      className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900"
                    >
                      <p className="font-bold flex items-center gap-1.5">
                        <span>⚠️</span> Prominent Uncertainty
                      </p>
                      <p className="mt-1 leading-relaxed">
                        Available evidence cannot confirm a primary cause. Do not release shipment based on incomplete evidence.
                      </p>
                      {unresolvedFinding?.missing_evidence?.length ? (
                        <div className="mt-2.5 border-t border-amber-200/70 pt-2">
                          <p className="font-bold text-[11px] text-amber-800 uppercase tracking-wider">
                            Missing evidence:
                          </p>
                          <ul className="mt-1 list-disc pl-4 space-y-1 text-[11px] text-amber-900">
                            {unresolvedFinding.missing_evidence.map((item, idx) => (
                              <li key={idx}>{item}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                  )}

                  {/* Actionable Next Checks */}
                  {report.next_checks.length > 0 && (
                    <div className="next-checks-section mt-5 border-t border-slate-100 pt-4">
                      <div className="flex items-center justify-between mb-2.5">
                        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-700">
                          Next checks
                        </h3>
                        <span className="text-[10px] text-slate-400 font-medium">
                          Operator verification actions
                        </span>
                      </div>
                      <div className="next-checks-list space-y-2">
                        {report.next_checks.map((check, idx) => (
                          <div key={check.code} className="next-check-item">
                            <span className="next-check-item__num">
                              {String(idx + 1).padStart(2, "0")}
                            </span>
                            <div className="next-check-item__body">
                              <p className="next-check-item__reason">{check.reason}</p>
                              <span className="next-check-item__code">
                                {check.code.replace(/_/g, " ").toUpperCase()}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </article>

            {/* Panel 3: Supporting & Conflicting Evidence Trail */}
            <article className="panel-card evidence-panel">
              <div className="panel-head">
                <div>
                  <span className="eyebrow">EVIDENCE TRAIL</span>
                  <h2>Supporting &amp; conflicting evidence</h2>
                </div>
                <span className="mini-status">{isReportReady ? `${evidence.length} citations` : "Pending"}</span>
              </div>

              <div className="mt-4">
                {!isReportReady ? (
                  <p className="text-xs text-slate-500">Evidence citations will appear once analysis completes.</p>
                ) : evidence.length === 0 ? (
                  <div className="evidence-empty-card p-4 rounded-xl border border-dashed border-slate-200 bg-slate-50/70 text-center">
                    <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">No cited evidence</span>
                    <p className="mt-1 text-xs font-semibold text-slate-700">No cited evidence for this report.</p>
                    <p className="mt-1 text-[11px] text-slate-500">
                      No evidence references were cited for this investigation. Deterministic measurements remain recorded in the report summary.
                    </p>
                  </div>
                ) : (
                  <div className="evidence-trail-list space-y-3">
                    {/* Supporting evidence group */}
                    <div className="evidence-group">
                      <div className="evidence-group__header">
                        <span className="evidence-group__tag evidence-group__tag--supporting">
                          SUPPORTING EVIDENCE
                        </span>
                        <span className="evidence-group__count">
                          {evidence.filter((i) => {
                            const id = "evidence_id" in i ? i.evidence_id : i.key;
                            return supportingIds.has(id);
                          }).length} cited
                        </span>
                      </div>

                      <div className="space-y-2 mt-2">
                        {evidence.map((item) => {
                          const id = "evidence_id" in item ? item.evidence_id : item.key;
                          const isConflicting = conflictingIds.has(id);
                          // Render non-conflicting in this primary section
                          if (isConflicting) return null;

                          const ids = "evidence_id" in item ? item.record_ids : item.recordIds;
                          const active = ids.some((recordId) => selectedRecordIds.has(recordId));
                          const title = "evidence_id" in item ? item.summary : item.title;
                          const kind = "kind" in item && item.kind ? String(item.kind).replace(/_/g, " ").toUpperCase() : "RECORD";
                          const observedAt = "observed_at" in item ? item.observed_at : ("observedAt" in item ? item.observedAt : undefined);
                          const recordCount = ids.length;

                          return (
                            <button
                              key={id}
                              type="button"
                              aria-pressed={active}
                              onClick={() => setSelectedRecordIds(active ? new Set() : new Set(ids))}
                              className={`evidence-card ${active ? "evidence-card--active" : ""}`}
                            >
                              <div className="evidence-card__top">
                                <span className="evidence-card__kind">{kind}</span>
                                {observedAt && (
                                  <span className="evidence-card__time">
                                    {formatTime(observedAt)} UTC
                                  </span>
                                )}
                              </div>
                              <p className="evidence-card__summary">{title}</p>
                              <div className="evidence-card__bottom">
                                <span className="evidence-card__records">
                                  {recordCount} record{recordCount === 1 ? "" : "s"}
                                </span>
                                <span className="evidence-card__arrow" aria-hidden="true">→</span>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Conflicting evidence group */}
                    <div className="evidence-group mt-4">
                      <div className="evidence-group__header">
                        <span className="evidence-group__tag evidence-group__tag--conflicting">
                          CONFLICTING EVIDENCE
                        </span>
                        <span className="evidence-group__count">
                          {conflictingIds.size} cited
                        </span>
                      </div>

                      {conflictingIds.size === 0 ? (
                        <div className="evidence-empty-conflicts">
                          <span className="evidence-empty-conflicts__icon">✓</span>
                          <span>No conflicting evidence recorded in this snapshot</span>
                        </div>
                      ) : (
                        <div className="space-y-2 mt-2">
                          {evidence.map((item) => {
                            const id = "evidence_id" in item ? item.evidence_id : item.key;
                            if (!conflictingIds.has(id)) return null;

                            const ids = "evidence_id" in item ? item.record_ids : item.recordIds;
                            const active = ids.some((recordId) => selectedRecordIds.has(recordId));
                            const title = "evidence_id" in item ? item.summary : item.title;
                            const kind = "kind" in item && item.kind ? String(item.kind).replace(/_/g, " ").toUpperCase() : "RECORD";
                            const observedAt = "observed_at" in item ? item.observed_at : ("observedAt" in item ? item.observedAt : undefined);
                            const recordCount = ids.length;

                            return (
                              <button
                                key={id}
                                type="button"
                                aria-pressed={active}
                                onClick={() => setSelectedRecordIds(active ? new Set() : new Set(ids))}
                                className={`evidence-card evidence-card--conflicting ${
                                  active ? "evidence-card--active" : ""
                                }`}
                              >
                                <div className="evidence-card__top">
                                  <span className="evidence-card__kind">{kind}</span>
                                  {observedAt && (
                                    <span className="evidence-card__time">
                                      {formatTime(observedAt)} UTC
                                    </span>
                                  )}
                                </div>
                                <p className="evidence-card__summary">{title}</p>
                                <div className="evidence-card__bottom">
                                  <span className="evidence-card__records">
                                    {recordCount} record{recordCount === 1 ? "" : "s"}
                                  </span>
                                  <span className="evidence-card__arrow" aria-hidden="true">→</span>
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </div>

                    <p className="text-[11px] text-slate-400 mt-2 italic text-center">
                      Select evidence to highlight corresponding records on the telemetry timeline.
                    </p>
                  </div>
                )}
              </div>
            </article>
          </section>

          {/* Operator Review & Report Export */}
          <section className="dashboard-chrome review-grid mt-4">
            <article className="review-card">
              <div className="panel-head">
                <div>
                  <span className="eyebrow">HUMAN REVIEW</span>
                  <h2>Operator review</h2>
                </div>
                <span className="mini-status mini-status--warning">REQUIRED</span>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                Review acknowledgement records operator review only and does not constitute shipment release or professional QA approval.
              </p>
              {!isReportReady ? (
                <p className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-500">
                  Operator review is enabled once the investigation report is complete.
                </p>
              ) : (
                <>
                  {reviewDecision && <p role="status" className="mt-2 text-[11px] text-green-700 font-medium">Review saved.</p>}
                  {currentReview ? (
                    <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-slate-950">
                          Recorded Review · {currentReview.decision.replace("_", " ")}
                        </span>
                        <span className="text-[11px] text-slate-500">
                          {new Date(currentReview.reviewed_at).toUTCString()}
                        </span>
                      </div>
                      <p className="mt-1 text-slate-600">
                        <strong>Reviewer Identity:</strong>{" "}
                        <span data-testid="reviewer-identity">{currentReview.actor_sub}</span>
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
                          {actions.reviewSubmitting && (
                            <svg className="btn-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                            </svg>
                          )}
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
                  <h2 className="text-base font-semibold">{isReportReady && report ? "Report ready" : "Report pending"}</h2>
                  <p
                    className={`text-[10px] ${
                      !isReportReady || !report
                        ? "text-slate-500"
                        : report.verification.status === "passed"
                        ? "text-green-700"
                        : "text-amber-700"
                    }`}
                  >
                    {!isReportReady || !report ? "Awaiting report generation" : verificationCopy}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={(!isPublicDemo && !isProtectedRun) || !isReportReady || !report}
                  onClick={() => void handleDownload()}
                  className="rounded-md bg-[#123B5D] px-3 py-2 text-[11px] font-semibold text-white disabled:opacity-50 hover:bg-[#0F304C]"
                >
                  Download
                </button>
              </div>
              {isReportReady && report && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-[10px] font-semibold text-slate-600">
                    Diagnostics
                  </summary>
                  <div className="mt-2 space-y-1 bg-slate-50 p-2.5 rounded text-[10px] text-slate-500 font-mono">
                    <p>Run ID: {run.run_id}</p>
                    <p>Report ID: {report.report_id}</p>
                    <p>Snapshot SHA-256: {report.snapshot_sha256}</p>
                    <p>Generation: {report.generation_mode}</p>
                  </div>
                </details>
              )}
            </article>
          </section>
        </div>

        {isReportReady && report && (
          <PrintableReport run={run} snapshot={snapshot} report={report} reviewOverride={currentReview} />
        )}
      </div>
    </main>
  );
}
