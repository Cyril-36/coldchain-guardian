.PHONY: test lint dev-api dev-web eval-offline eval-bedrock build smoke-cloud

test:
	cd backend && python -m pytest tests/ -v

lint:
	cd backend && python -m ruff check coldchain tests

dev-api:
	cd backend && python -m coldchain.dev.harness

dev-web:
	cd apps/web && npm run dev

eval-offline:
	cd backend && python -m pytest tests/ -v -m "not bedrock"

eval-bedrock:
	cd backend && python -m pytest tests/ -v -m bedrock

build:
	cd infra && sam build

smoke-cloud:
	@echo "Run manually with AWS credentials: see docs/VERIFICATION.md"
