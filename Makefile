PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install services test test-unit fuzz cov lint fmt typecheck check migrate seed verify-chain revision run ui generate-types clean

install:
	$(PIP) install -q -e ".[dev,pdf,oidc]"

services:
	docker compose up -d
	@echo "postgres :5435"

test:
	$(PY) -m pytest

# Unit + property tests only; no services required.
test-unit:
	$(PY) -m pytest -m "not integration"

# Property-based HTTP fuzzing alone (idea #7, AUDIT_AND_BREAKTHROUGHS.md) --
# needs Postgres, same as integration.
fuzz:
	$(PY) -m pytest -m fuzz

cov:
	$(PY) -m pytest --cov --cov-report=term-missing

lint:
	.venv/bin/ruff check src tests migrations
	.venv/bin/ruff format --check src tests migrations

fmt:
	.venv/bin/ruff format src tests migrations
	.venv/bin/ruff check --fix src tests migrations

typecheck:
	.venv/bin/mypy src

check: lint typecheck test

migrate:
	.venv/bin/alembic upgrade head

# Reference data the lab cannot operate without; safe to re-run.
seed:
	$(PY) -m msa_lims.db.seed

# Recompute every audit_event hash independently and compare against what's
# stored (audit idea #1, AUDIT_AND_BREAKTHROUGHS.md). Exits non-zero on a
# broken chain.
verify-chain:
	$(PY) -m msa_lims.db.verify_chain

revision:
	.venv/bin/alembic revision --autogenerate -m "$(m)"

run:
	.venv/bin/uvicorn msa_lims.web.app:app --reload --port 8002

ui:
	cd frontend && npm run dev

# Regenerate frontend/src/generated-types.ts from the live app's own
# OpenAPI schema (idea #18, AUDIT_AND_BREAKTHROUGHS.md) -- no server needed.
# Run this after any request/response schema change and commit the result;
# CI's `check-types-drift` step fails if it would have produced a diff.
generate-types:
	$(PY) -m msa_lims.web.export_openapi frontend/openapi.json
	cd frontend && npm run generate-types

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
