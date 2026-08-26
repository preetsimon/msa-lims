"""The audit chain verification endpoint, through the real FastAPI app."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from msa_lims.web.app import create_app
from msa_lims.web.deps import get_db

pytestmark = pytest.mark.integration


@pytest.fixture
def session(app_engine: Engine) -> Iterator[Session]:
    connection = app_engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def app(session: Session) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_db] = lambda: session
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


SUPERVISOR = {"X-Actor": "sup@lab", "X-Actor-Role": "supervisor"}
ANALYST = {"X-Actor": "analyst@lab", "X-Actor-Role": "analyst"}
CLIENT_ROLE = {"X-Actor": "geo@mineco", "X-Actor-Role": "client"}


def recipe_body(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "name": "Standard Silicate",
        "matrix_type": "silicate",
        "nominal_portion_g": "30",
        "litharge_g": "60",
        "soda_ash_g": "90",
        "borax_g": "30",
        "silica_g": "15",
        "flour_g": "3",
        "nitre_g": "0",
    }
    defaults.update(overrides)
    return defaults


class TestVerifyingTheChainThroughHttp:
    def test_an_empty_lab_verifies_trivially(self, client: TestClient) -> None:
        response = client.get("/api/audit/verify", headers=ANALYST)
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "valid": True,
            "verified_count": 0,
            "head_hash": None,
            "broke_at_id": None,
            "broke_reason": None,
        }

    def test_a_real_write_produces_a_verifying_chain(self, client: TestClient) -> None:
        created = client.post("/api/flux-recipes", json=recipe_body(), headers=SUPERVISOR)
        assert created.status_code == 201

        response = client.get("/api/audit/verify", headers=ANALYST)
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["verified_count"] == 1
        assert body["head_hash"] is not None
        assert len(body["head_hash"]) == 64

    def test_upto_actually_restricts_the_scan(self, client: TestClient) -> None:
        """``upto=0`` excludes every real row (`audit_event.id` starts at 1)
        — proves the query parameter genuinely reaches the filter rather
        than being accepted and ignored. A specific positive id boundary is
        exercised at the service level (`test_audit_service.py`); the
        real id here is unknown from the HTTP layer alone, since
        `audit_event`'s id sequence is not transactional and keeps
        advancing across every other test that has run in this session."""
        client.post("/api/flux-recipes", json=recipe_body(), headers=SUPERVISOR)

        full = client.get("/api/audit/verify", headers=ANALYST).json()
        assert full["verified_count"] == 1

        restricted_response = client.get("/api/audit/verify?upto=0", headers=ANALYST)
        assert restricted_response.status_code == 200
        restricted = restricted_response.json()
        assert restricted == {
            "valid": True,
            "verified_count": 0,
            "head_hash": None,
            "broke_at_id": None,
            "broke_reason": None,
        }

    def test_a_client_role_is_refused_with_403(self, client: TestClient) -> None:
        response = client.get("/api/audit/verify", headers=CLIENT_ROLE)
        assert response.status_code == 403
