"""Fixtures for tests that need a real Postgres.

Integration tests run against a **dedicated test database**, created and migrated
on first use, never against the development database. That is not fastidiousness:
the suite makes assertions about counts and about "the only submission", and any
developer who had entered a sample locally would otherwise see those tests fail
for reasons that have nothing to do with their change.

Within that database each test runs inside a transaction that is rolled back, so
tests neither see nor leave each other's rows. The session is bound to the
**restricted application role**, which means every service is exercised under
exactly the grants it will have in production — a service that needed a
forbidden grant fails here rather than in deployment.

Everything skips cleanly when Postgres is unreachable, so the unit and property
suites still run on a laptop with nothing installed but Python.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from msa_lims.config import get_settings

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DATABASE_NAME = "msa_test"


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


@pytest.fixture(scope="session")
def test_database() -> tuple[str, str]:
    """Ensure the test database exists and is migrated. Returns (app, owner) URLs.

    Creating a database cannot happen inside a transaction, hence the AUTOCOMMIT
    connection to ``postgres``. The migration then runs as the schema owner, so
    the append-only grants land exactly as they do in a real deployment.
    """
    settings = get_settings()
    owner_url = _with_database(settings.migration_database_url, TEST_DATABASE_NAME)
    app_url = _with_database(settings.database_url, TEST_DATABASE_NAME)

    admin = create_engine(
        _with_database(settings.migration_database_url, "postgres"),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with admin.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DATABASE_NAME},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE_NAME}"'))
    except OperationalError as exc:
        pytest.skip(f"Postgres unreachable ({exc.__class__.__name__}); run `docker compose up -d`")
    finally:
        admin.dispose()

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", owner_url)
    command.upgrade(config, "head")

    return app_url, owner_url


@pytest.fixture(scope="session")
def app_engine(test_database: tuple[str, str]) -> Iterator[Engine]:
    """Connected as the restricted application role."""
    engine = create_engine(test_database[0], future=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def owner_engine(test_database: tuple[str, str]) -> Iterator[Engine]:
    """Connected as the schema owner, as migrations are."""
    engine = create_engine(test_database[1], future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def app_session(app_engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=app_engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
