# Agent rules for ColdChain Guardian

These instructions apply to the entire repository. Read README.md, PLAN.md, docs/CONTRACTS.md and your assignment before editing. This repository is currently a plan; do not describe planned services or tests as implemented.

## People, branches and ownership

| Contributor | Branch | Owns |
|---|---|---|
| Cyril | `feat/investigation-core` | Contracts, detector, evidence tools, Strands/Bedrock investigator, verifier, worker, infrastructure, evaluation and release |
| Harshith | `feat/data-api` | Simulator, storage adapters, API and their tests |
| Navadeep | `feat/dashboard` | React UI, chart, typed client, app login and UI tests |

Treat these branches as workstreams, not permanent integration branches. Make small commits and open PRs to `main` at each milestone. After a PR merges, update your branch from `main` before the next change. Never push directly to `main`; never force-push shared branches. Edit outside your ownership only after discussing the exact change in the PR or team channel. Changes to contracts need Cyril's review before code depending on them is merged.

## Source of truth

- `docs/CONTRACTS.md` controls schemas, statuses and HTTP routes until generated schemas exist.
- `PLAN.md` controls scope and AWS architecture.
- Assignment documents control who implements each part.
- `docs/VERIFICATION.md` controls acceptance. A passing build is not proof that incident reasoning is correct.
- Real code and measured test output take precedence over claims in a PR description.

If documents disagree, call out the precise conflict in the PR and have Cyril resolve it. Do not quietly invent a new endpoint or field.

## Project rules

1. Work begins within the official event window. Keep a truthful Git history and credit third-party dependencies and assets. List AI coding tools in the submission writeup.
2. Use synthetic telemetry for the MVP. Do not imply a real truck or sensor exists.
3. Keep scenario labels and expected outcomes out of the investigator's inputs, tool outputs and deployed runtime package.
4. Numeric excursion metrics come from deterministic code. AI may compare explanations and select cited evidence; it must not invent readings, durations, probabilities or medical disposition decisions.
5. Every report links evidence IDs to a frozen snapshot and preserves missing/contradictory evidence. When evidence is insufficient, return `unresolved` or `needs_review`.
6. An acknowledgement is not shipment release or professional product approval.
7. No hardcoded AWS credentials, API keys, account IDs, private data or tokens in code, logs, issues or PRs. Commit only `.env.example`, never real `.env` files.
8. Only Cyril deploys to the team's credited AWS account. Harshith and Navadeep develop with fixtures, fakes and the loopback local API. They do not need AWS IAM access or a separate credit claim.
9. Keep AWS usage bounded by the server-side run, model-call, time and concurrency limits in PLAN.md. Do not add an always-on or provisioned paid service without updating the plan and review.
10. Tests must check behaviour and failure paths. Do not write tests that merely duplicate implementation logic or assert static text.

## Before opening a PR

- Run the applicable offline tests, lint/type checks and contract example validator. Paste exact command results in the PR.
- Include a sample response or UI screenshot if behaviour is visible.
- State any changed contract and migration needed by another workstream.
- State unverified cloud behaviour honestly. Cyril runs cloud smoke tests on integrated work.
- Verify no secrets or generated build artifacts are staged.

PRs target `main`. At least one other team member must approve before merging; the author cannot approve their own PR. Resolve review conversations and pass required CI checks. Cyril performs final integration and merge decisions. Review of Cyril's PRs should be done by Harshith or Navadeep. Do not bypass branch protection to save time.

### Admin merge exception

Branch protection no longer blocks repository admins, so the remaining discipline is a team rule rather than a technical control. A repository admin may merge a submission-day blocker PR without external approval only when all of the following hold:

- required CI checks are green,
- the diff has been reviewed,
- the relevant tests pass,
- and no failing required check is bypassed.

Admin bypass can skip more than approvals. Never merge with a failing or pending required check, even when GitHub permits it. A doc-only or non-blocking change does not qualify for this exception; request a teammate review as usual. Record the reason for any admin merge in the PR description.

## Priority when time is short

Required: one reliable end-to-end AWS investigation and honest uncertainty. Recommended soon: five scenario coverage, stronger evaluation and user feedback. Optional: map animation, IoT Core and multiple agents. Preserve a known-good main branch for the submission.
