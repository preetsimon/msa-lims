"""Instrument registry — API endpoint tests."""

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


ANALYST = {"X-Actor": "analyst@lab", "X-Actor-Role": "analyst"}
SUPERVISOR = {"X-Actor": "supervisor@lab", "X-Actor-Role": "supervisor"}


def test_create_instrument(client: TestClient) -> None:
    response = client.post(
        "/api/instruments",
        json={
            "name": "ICP-OES-01",
            "instrument_type": "icp_oes",
            "manufacturer": "Agilent",
            "model": "5800",
            "location": "Lab B",
        },
        headers=SUPERVISOR,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "ICP-OES-01"
    assert data["instrument_type"] == "icp_oes"
    assert data["status"] == "active"


def test_create_instrument_duplicate_name(client: TestClient) -> None:
    client.post(
        "/api/instruments",
        json={"name": "ICP-OES-01", "instrument_type": "icp_oes"},
        headers=SUPERVISOR,
    )
    response = client.post(
        "/api/instruments",
        json={"name": "ICP-OES-01", "instrument_type": "icp_oes"},
        headers=SUPERVISOR,
    )
    assert response.status_code == 409


def test_list_instruments(client: TestClient) -> None:
    client.post(
        "/api/instruments",
        json={"name": "AAS-01", "instrument_type": "aas"},
        headers=SUPERVISOR,
    )
    client.post(
        "/api/instruments",
        json={"name": "ICP-01", "instrument_type": "icp_ms"},
        headers=SUPERVISOR,
    )

    response = client.get("/api/instruments", headers=ANALYST)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


def test_list_instruments_filter_by_type(client: TestClient) -> None:
    client.post(
        "/api/instruments",
        json={"name": "AAS-01", "instrument_type": "aas"},
        headers=SUPERVISOR,
    )
    client.post(
        "/api/instruments",
        json={"name": "ICP-01", "instrument_type": "icp_ms"},
        headers=SUPERVISOR,
    )

    response = client.get("/api/instruments?instrument_type=aas", headers=ANALYST)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "AAS-01"


def test_read_instrument(client: TestClient) -> None:
    create_resp = client.post(
        "/api/instruments",
        json={"name": "ICP-OES-01", "instrument_type": "icp_oes"},
        headers=SUPERVISOR,
    )
    instrument_id = create_resp.json()["id"]

    response = client.get(f"/api/instruments/{instrument_id}", headers=ANALYST)
    assert response.status_code == 200
    assert response.json()["name"] == "ICP-OES-01"


def test_read_instrument_not_found(client: TestClient) -> None:
    response = client.get("/api/instruments/99999", headers=ANALYST)
    assert response.status_code == 404


def test_update_instrument(client: TestClient) -> None:
    create_resp = client.post(
        "/api/instruments",
        json={"name": "ICP-OES-01", "instrument_type": "icp_oes"},
        headers=SUPERVISOR,
    )
    instrument_id = create_resp.json()["id"]

    response = client.patch(
        f"/api/instruments/{instrument_id}",
        json={"location": "Lab C", "status": "maintenance"},
        headers=SUPERVISOR,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["location"] == "Lab C"
    assert data["status"] == "maintenance"
    assert data["name"] == "ICP-OES-01"


def test_update_instrument_name_conflict(client: TestClient) -> None:
    client.post(
        "/api/instruments",
        json={"name": "ICP-OES-01", "instrument_type": "icp_oes"},
        headers=SUPERVISOR,
    )
    other = client.post(
        "/api/instruments",
        json={"name": "ICP-MS-01", "instrument_type": "icp_ms"},
        headers=SUPERVISOR,
    )
    other_id = other.json()["id"]

    response = client.patch(
        f"/api/instruments/{other_id}",
        json={"name": "ICP-OES-01"},
        headers=SUPERVISOR,
    )
    assert response.status_code == 409


def test_create_instrument_insufficient_role(client: TestClient) -> None:
    response = client.post(
        "/api/instruments",
        json={"name": "ICP-OES-01", "instrument_type": "icp_oes"},
        headers=ANALYST,
    )
    assert response.status_code == 403


def test_update_instrument_insufficient_role(client: TestClient) -> None:
    create_resp = client.post(
        "/api/instruments",
        json={"name": "ICP-OES-01", "instrument_type": "icp_oes"},
        headers=SUPERVISOR,
    )
    instrument_id = create_resp.json()["id"]

    response = client.patch(
        f"/api/instruments/{instrument_id}",
        json={"location": "Lab C"},
        headers=ANALYST,
    )
    assert response.status_code == 403


def test_create_microbalance_with_sensitivity(client: TestClient) -> None:
    response = client.post(
        "/api/instruments",
        json={
            "name": "MB-01",
            "instrument_type": "microbalance",
            "balance_sensitivity_mg": "0.001",
        },
        headers=SUPERVISOR,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["balance_sensitivity_mg"] == "0.001"
    assert data["solution_detection_limit"] is None


def test_create_icp_with_detection_limit(client: TestClient) -> None:
    response = client.post(
        "/api/instruments",
        json={
            "name": "ICP-OES-01",
            "instrument_type": "icp_oes",
            "solution_detection_limit": "0.05",
        },
        headers=SUPERVISOR,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["solution_detection_limit"] == "0.05"
    assert data["balance_sensitivity_mg"] is None


def test_update_instrument_sensitivity(client: TestClient) -> None:
    create_resp = client.post(
        "/api/instruments",
        json={"name": "MB-02", "instrument_type": "microbalance"},
        headers=SUPERVISOR,
    )
    instrument_id = create_resp.json()["id"]

    response = client.patch(
        f"/api/instruments/{instrument_id}",
        json={"balance_sensitivity_mg": "0.005", "reason": "calibration update"},
        headers=SUPERVISOR,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["balance_sensitivity_mg"] == "0.005"
