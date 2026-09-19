# ColdChain Guardian — repository commands (docs/VERIFICATION.md section 5).
# A clean clone must reproduce every offline check without AWS credentials.

PY ?= python3
WEB := apps/web
REGION ?= $(AWS_REGION)
MODEL ?= $(BEDROCK_MODEL_ID)

.PHONY: help install test lint eval-offline eval-bedrock build probe-bedrock reconcile

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

install: ## Install backend and frontend dependencies
	$(PY) -m pip install -e 'backend[dev]'
	cd $(WEB) && npm install --no-audit --no-fund

test: ## Offline Python and frontend tests
	$(PY) -m pytest backend/tests eval/tests
	cd $(WEB) && npm test -- --run

lint: ## Ruff plus frontend typecheck, and the contract example validator
	$(PY) -m ruff check scripts backend eval .
	$(PY) scripts/check_repo.py
	$(PY) scripts/validate_examples.py
	cd $(WEB) && npm run typecheck

eval-offline: ## Frozen holdout set against the rule baseline; no model calls
	$(PY) -m pytest backend/tests/core backend/tests/investigation eval/tests -q
	$(PY) scripts/run_eval.py --proposer baseline

eval-bedrock: ## Bounded live-model evaluation; must be enabled explicitly
	@test "$(EVAL_BEDROCK)" = "1" || { echo 'Refusing to spend model calls. Re-run with EVAL_BEDROCK=1'; exit 1; }
	@test -n "$(REGION)" || { echo 'Set AWS_REGION'; exit 1; }
	@test -n "$(MODEL)" || { echo 'Set BEDROCK_MODEL_ID'; exit 1; }
	$(PY) scripts/probe_bedrock.py --region "$(REGION)" --model-id "$(MODEL)"
	$(PY) scripts/run_eval.py --proposer bedrock --region "$(REGION)" --model-id "$(MODEL)"

probe-bedrock: ## One bounded tool-call and structured-output probe
	$(PY) scripts/probe_bedrock.py --region "$(REGION)" --model-id "$(MODEL)"

# Make treats any non-empty value as true, so a bare $(if $(APPLY)) would let
# APPLY=0 and APPLY=false mutate. Only these exact words opt in.
APPLY_FLAG := $(if $(filter 1 true yes on,$(APPLY)),--apply,)

reconcile: ## Dry-run DLQ/stale-run reconciliation (APPLY=1 to make changes)
	$(PY) scripts/reconcile_runs.py --table "$(RUNS_TABLE)" --bucket "$(ARTIFACT_BUCKET)" \
	  --region "$(REGION)" --dlq-url "$(DLQ_URL)" $(APPLY_FLAG)

build: ## SAM build and frontend production build
	sam build --template-file infra/template.yaml
	cd $(WEB) && npm run build
