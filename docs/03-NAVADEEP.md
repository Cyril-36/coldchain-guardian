# Navadeep’s work — dashboard and review experience

Your task is a polished, understandable interface for the actual backend. Cyril owns the investigation and deployment. Harshith owns the API. You do not need to infer temperature limits or calculate causes.

Read [master plan](../PLAN.md) and [shared contracts](CONTRACTS.md). Own apps/web and its tests. Branch: feat/dashboard.

## Stack and boundaries

React + TypeScript + Vite, Tailwind CSS, shadcn/ui, Lucide icons, Recharts and TanStack Query. Use generated contract types. For Cognito managed login, use a maintained OIDC client supporting authorization code with PKCE; leader approves the selected package/version during the first integration. Do not write cryptography or store AWS credentials in browser code.

Environment config contains only public values: VITE_API_BASE_URL, VITE_COGNITO_AUTHORITY, VITE_COGNITO_CLIENT_ID, VITE_COGNITO_REDIRECT_URI. Prefixing something with VITE makes it visible in the build; never put a secret there.

## Deliverable 1 — one focused dashboard

Use this layout:

```text
ColdChain Guardian                 Simulated shipment · AWS-backed processing
Demo case selector / operator login / run button

[Observed peak] [Estimated time out of range] [Data coverage] [Review status]

Temperature chart with configured band and aligned door/equipment events

Investigation stages              Main finding / unresolved result
Actual tool activity              Supporting and conflicting evidence

Next checks                       Review note and acknowledgement
Report download / print
```

Use plain product terms: “Temperature exceeded the configured limit”, “Possible explanation”, “Missing evidence”, “Needs review”. Keep raw service names and request IDs in an expandable diagnostics view, not the primary user journey.

Start with leader-provided fixtures, including unresolved/model-unavailable/failed states. Mock mode must display a development banner and be disabled in the release build. Do not build against hardcoded happy-path assumptions.

## Deliverable 2 — chart and evidence navigation

- Plot both sensors with distinguishable colours AND labels/line styles.
- Show the allowed temperature band from the API policy, not hardcoded 2–8 constants.
- Mark door and refrigeration events on the same time axis.
- Render data gaps as gaps: do not visually connect missing intervals as if observed.
- Tooltip includes timestamp, timezone, sensor ID, observed value and event ID.
- Clicking a report evidence item highlights or scrolls to the relevant chart/event records.
- Distinguish observed peak from estimated duration. Display coverage limitations beside the duration.
- At the window edges, show incomplete exposure duration when the backend reports censored readings.

Use backend metrics verbatim. Do not recompute exposure in JavaScript, infer diagnosis from chart shape, or generate confidence scores. An optional animated replay is labeled “recorded scenario replay”; it must not masquerade as a live sensor feed.

## Deliverable 3 — live API state and authentication

Create one typed API client with response validation and the common error format. Read the frozen endpoint list. Public visitors can browse curated completed simulations using public demo paths. Only signed-in team operators can create runs and submit reviews using protected paths.

After POST runs, poll status every two seconds while active; back off after thirty seconds and pause while the tab is hidden. Stop polling terminal states. Preserve run_id in the URL so refresh reopens the same run. Do not create a new run on refresh or retry with a fresh idempotency key after an uncertain response.

Keep an Idempotency-Key for the pending submission; retry the same key/body on a retryable network failure. Disable duplicate clicks while submitting. A deliberate new run gets a new key.

Use actual backend stages and tool events. Never animate completed checkmarks on timers. Show concise technical errors as user actions: retry submission, refresh status or view the deterministic report. A slow job is not a failed job unless the backend says so.

Use Cognito managed login settings supplied by the leader. Send the access token, not ID token, to operator endpoints. Clear local auth state on sign-out. Do not log tokens or commit test credentials. Confirm callback refresh and expired-session behaviour with the leader.

## Deliverable 4 — report and review

Report page includes shipment/snapshot IDs, cutoff/timezone, policy version, simulated-data notice, measurements, likely explanation or unresolved result, evidence, contradictions, limitations and next checks. A verification badge says “Evidence references and measurements checked”; it must not imply medical approval.

Review form offers Acknowledge review and Request more evidence with a note. Explain that neither action releases the shipment. Display who reviewed and when using backend identity, not a name field the browser invents. Bind submission to report_id.

Provide JSON download through the authorized URL endpoint and a clean print stylesheet for browser Save as PDF. Keep limitations and simulation labels in printed output. No PDF generation service is needed.

## Required states

| State | Expected UI |
|---|---|
| Initial visitor | Public completed demo plus option to inspect another case |
| Submitting/queued | Disabled repeat-submit, real status text |
| Running | Chart/evidence available; actual processing stage |
| Supported hypothesis | Evidence-linked explanation and alternatives |
| Unresolved | Prominent uncertainty and next missing check |
| Model unavailable | Deterministic metrics with explicit AI-unavailable label |
| No excursion | No detected excursion in this snapshot; no product-safety claim |
| Failure | Clear error, retained run ID, valid retry/refresh action |
| Partial/missing data | Visible gaps and duration qualification |
| Signed out/private URL | Login request without disclosing private report data |

## Tests and acceptance

Vitest: status mapping, unresolved presentation, evidence selection and retry key reuse. Avoid tests that merely assert every static heading exists.

Playwright: open public demo; switch case; follow evidence link; print view retains limitations; login/operator run with the test setup; refresh while processing; review result; expired-session handling. Use contract fixtures for UI development, then at least one end-to-end run against AWS before acceptance.

Manually check at laptop and mobile widths, keyboard-only navigation, focus indicators and chart/table text alternatives. Use symbols/text as well as colour. Avoid a large decorative map that hides the report.

## Suggested PRs

1. Page shell, typed client and all fixture states.
2. Chart, event timeline and evidence interactions.
3. Real API integration, polling and managed login.
4. Review/export, accessibility, error paths and deployment-ready build.

Hand off the actual build command, output directory (dist), public environment variables, screenshots of the supported/unresolved states and test results. Leader deploys using Amplify; you verify deep-link refresh and the final URL.

## Definition of done

A new visitor understands the incident and can inspect its evidence without guidance. An operator can create, monitor and review a real backend run. No frontend-generated diagnosis, fake progress, hidden data gaps or exposed credentials remain.

Required: accurate dashboard and full states. Recommended soon: user feedback on terminology. Optional: map animation only after core acceptance passes.
