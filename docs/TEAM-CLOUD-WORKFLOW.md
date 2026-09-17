# One credited AWS account, three developers

Confirmed plan: Cyril owns the team's credited AWS account and performs all cloud deployment, Bedrock calls, billing checks and infrastructure changes. Harshith and Navadeep build locally and submit PRs. There are no separate team-member deployments or separate credit claims in this workflow.

The organiser email says one person per team fills in the credit form and receives the code by email. That is a team credit-claim instruction; it does not technically imply only one developer can contribute to an AWS-hosted project. This plan deliberately centralises AWS administration with Cyril, as requested. [Official event page](https://www.wemakedevs.org/aws/first-commit).

## Before cloud work

Cyril checks that the credit code has actually been redeemed into the intended AWS account, verifies remaining credit and service coverage, and confirms model access. The user previously confirmed account/credit availability, but this plan does not independently verify redemption. If the code is still pending, the local work below proceeds immediately; cloud spending begins only when Cyril has confirmed the intended billing setup.

## Local work with no AWS account

| Person | Local work | Needs AWS credentials? |
|---|---|---|
| Cyril | Core detector, investigator interfaces, verifier, schemas and offline evaluation | Only for cloud/Bedrock checks |
| Harshith | Simulator, API functions, memory storage, AWS adapters tested with fakes | No |
| Navadeep | Dashboard with schema-valid fixtures or local API | No |

Cyril first provides make dev-api: a loopback-only local HTTP harness using the same route/business functions and an in-memory storage adapter. It binds to 127.0.0.1, supplies a fixed synthetic operator identity, returns clearly labeled local execution, and is excluded from production bundles. This keeps local work independent of Cognito and cloud credentials. The team does not need LocalStack for the first iteration.

Local development does not prove AWS adapters work. Cyril runs the separate cloud gates on merged changes and shares sanitized failures with the owner. Harshith fixes adapter/API defects; Navadeep fixes UI integration defects; Cyril reruns the relevant check.

## Integration handoffs

1. Harshith hands Cyril tested backend code, environment variable names, memory-test results and any IAM operations required by adapters. No credentials are requested.
2. Navadeep hands Cyril the frontend build, public configuration names, dist output and test results.
3. Cyril deploys SAM, configures the worker/model, deploys Amplify and sets callback/CORS URLs.
4. Cyril shares the API/app URLs, public Cognito configuration, contract version and known limitations.
5. If needed, Cyril creates individual app-level operator users for Harshith and Navadeep. They can test the application without receiving an AWS IAM user, account password or access keys.
6. Teammates use the same shared deployment for integration. All paid runs are bounded by the server-side caps in PLAN.md.

Do not share root login, MFA codes, access keys, local AWS credential files or billing access. Deployment is manual by Cyril for the weekend; CI runs offline tests and does not need AWS secrets. A future automated deploy can use GitHub OIDC with a narrowly scoped role after the MVP.

## Daily coordination

Have two short integration checkpoints, not continuous requests for account access. Each teammate reports: completed PR, contract question if any, test result and next deliverable. Cyril merges accepted changes, deploys one integrated build and sends back the exact failing request/run ID when a defect appears.

Keep a last-known-good deployment and commit. If a late PR fails cloud smoke checks, restore that known release while the owner fixes the branch. Do not build three parallel cloud stacks just to unblock local coding.
