PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install services test test-unit cov lint fmt typecheck check migrate seed revision run ui clean

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

revision:
	.venv/bin/alembic revision --autogenerate -m "$(m)"

run:
	.venv/bin/uvicorn msa_lims.web.app:app --reload --port 8002

ui:
	cd frontend && npm run dev

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
