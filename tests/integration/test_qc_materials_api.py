"""QC material registration, through the real FastAPI app."""

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


def crm_body(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "name": "OREAS 501d",
        "qc_type": "crm",
        "lot_number": "LOT-2026-A",
        "certified_au_value_g_t": "1.54",
        "certified_au_uncertainty_g_t": "0.06",
        "notes": None,
    }
    defaults.update(overrides)
    return defaults


class TestRegisteringAMaterial:
    def test_a_supervisor_registers_a_crm(self, client: TestClient) -> None:
        response = client.post("/api/qc-materials", json=crm_body(), headers=SUPERVISOR)
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "OREAS 501d"
        assert body["qc_type"] == "crm"
        assert body["certified_au_value_g_t"] == "1.54"
        assert body["is_active"] is True

    def test_an_analyst_is_refused_with_403(self, client: TestClient) -> None:
        response = client.post("/api/qc-materials", json=crm_body(), headers=ANALYST)
        assert response.status_code == 403

    def test_a_duplicate_name_is_422(self, client: TestClient) -> None:
        client.post("/api/qc-materials", json=crm_body(), headers=SUPERVISOR)
        response = client.post("/api/qc-materials", json=crm_body(), headers=SUPERVISOR)
        assert response.status_code == 422

    def test_a_duplicate_qc_type_is_422_naming_the_remedy(self, client: TestClient) -> None:
        response = client.post(
            "/api/qc-materials",
            json=crm_body(qc_type="field_duplicate"),
            headers=SUPERVISOR,
        )
        assert response.status_code == 422
        assert "not a material" in response.json()["detail"]

    def test_a_crm_without_a_certified_value_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/qc-materials",
            json=crm_body(certified_au_value_g_t=None, certified_au_uncertainty_g_t=None),
            headers=SUPERVISOR,
        )
        assert response.status_code == 422

    def test_a_blank_with_a_certified_value_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/qc-materials",
            json=crm_body(qc_type="blank"),
            headers=SUPERVISOR,
        )
        assert response.status_code == 422
