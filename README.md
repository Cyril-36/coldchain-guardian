# ColdChain Guardian

ColdChain Guardian is a planned AWS-hosted investigation dashboard for simulated refrigerated shipments. It aims to turn a temperature excursion into a report with measurements, evidence, competing explanations and a human review step.

**Status:** Planning documents only. The application has not yet been implemented or deployed. This repository is for the WeMakeDevs First Commit hackathon, September 17–20, 2026.

## Start here

- [Implementation plan](PLAN.md)
- [Rules for coding agents and pull requests](AGENTS.md)
- [Shared API and data contracts](docs/CONTRACTS.md)
- [Cyril’s work: investigation, verification and deployment](docs/01-CYRIL.md)
- [Harshith’s work: simulator, storage and API](docs/02-HARSHITH.md)
- [Navadeep’s work: dashboard and review experience](docs/03-NAVADEEP.md)
- [One AWS account, three developers](docs/TEAM-CLOUD-WORKFLOW.md)
- [Verification and release gates](docs/VERIFICATION.md)

Cyril owns the credited AWS account and deployment. Harshith and Navadeep develop locally, submit PRs and test against the shared application deployment. The project will use synthetic telemetry for the hackathon and will not determine whether medicines are safe to use.
