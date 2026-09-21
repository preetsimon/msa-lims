"""Tests for P2 admin endpoints: drill-holes list, projects list, audit events,
client detail, dashboard stats, and prep-records list."""

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
PREP = {"X-Actor": "prep@lab", "X-Actor-Role": "prep_tech"}
CLIENT = {"X-Actor": "geo@mineco", "X-Actor-Role": "client"}


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------


class TestDashboardStats:
    def test_stats_returns_counts(self, client: TestClient) -> None:
        response = client.get("/api/stats", headers=ANALYST)
        assert response.status_code == 200
        body = response.json()
        assert "total_samples" in body
        assert "total_clients" in body
        assert "samples_by_status" in body
        assert isinstance(body["samples_by_status"], dict)

    def test_client_role_refused(self, client: TestClient) -> None:
        response = client.get("/api/stats", headers=CLIENT)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Client detail
# ---------------------------------------------------------------------------


class TestClientDetail:
    def test_get_client_by_id(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/clients",
            json={"code": "TST01", "name": "Test Client"},
            headers=SUPERVISOR,
        )
        client_id = create_resp.json()["id"]
        response = client.get(f"/api/clients/{client_id}", headers=SUPERVISOR)
        assert response.status_code == 200
        assert response.json()["name"] == "Test Client"

    def test_get_nonexistent_client_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/clients/99999", headers=SUPERVISOR)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Projects list
# ---------------------------------------------------------------------------


class TestProjectsList:
    def test_list_projects(self, client: TestClient) -> None:
        client_resp = client.post(
            "/api/clients", json={"code": "PLC01", "name": "Proj Client"}, headers=SUPERVISOR
        )
        client_id = client_resp.json()["id"]
        client.post(
            "/api/projects",
            json={"client_id": client_id, "name": "Test Project"},
            headers=SUPERVISOR,
        )
        response = client.get("/api/projects", headers=SUPERVISOR)
        assert response.status_code == 200
        assert len(response.json()) >= 1

    def test_filter_projects_by_client(self, client: TestClient) -> None:
        c1 = client.post("/api/clients", json={"code": "C1", "name": "C1"}, headers=SUPERVISOR).json()
        c2 = client.post("/api/clients", json={"code": "C2", "name": "C2"}, headers=SUPERVISOR).json()
        client.post(
            "/api/projects",
            json={"client_id": c1["id"], "name": "P1"},
            headers=SUPERVISOR,
        )
        client.post(
            "/api/projects",
            json={"client_id": c2["id"], "name": "P2"},
            headers=SUPERVISOR,
        )
        response = client.get("/api/projects", headers=SUPERVISOR, params={"client_id": c1["id"]})
        assert response.status_code == 200
        assert all(p["client_id"] == c1["id"] for p in response.json())


# ---------------------------------------------------------------------------
# Drill holes list
# ---------------------------------------------------------------------------


class TestDrillHolesList:
    def test_list_drill_holes(self, client: TestClient) -> None:
        c = client.post("/api/clients", json={"code": "DH01", "name": "DH Client"}, headers=SUPERVISOR).json()
        p = client.post(
            "/api/projects",
            json={"client_id": c["id"], "name": "DH Project"},
            headers=SUPERVISOR,
        ).json()
        client.post(
            "/api/drill-holes",
            json={"project_id": p["id"], "hole_id": "MSA-26-100"},
            headers=PREP,
        )
        response = client.get("/api/drill-holes", headers=ANALYST)
        assert response.status_code == 200
        hole_ids = [h["hole_id"] for h in response.json()]
        assert "MSA-26-100" in hole_ids


# ---------------------------------------------------------------------------
# Audit events
# ---------------------------------------------------------------------------


class TestAuditEvents:
    def test_list_audit_events(self, client: TestClient) -> None:
        client.post("/api/clients", json={"code": "AUD01", "name": "Audit Client"}, headers=SUPERVISOR)
        response = client.get("/api/audit/events", headers=SUPERVISOR)
        assert response.status_code == 200
        assert len(response.json()) >= 1

    def test_filter_by_table_name(self, client: TestClient) -> None:
        client.post("/api/clients", json={"code": "AUF01", "name": "Audit Filter"}, headers=SUPERVISOR)
        response = client.get(
            "/api/audit/events", headers=SUPERVISOR, params={"table_name": "client"}
        )
        assert response.status_code == 200
        assert all(e["table_name"] == "client" for e in response.json())


# ---------------------------------------------------------------------------
# Prep records list
# ---------------------------------------------------------------------------


class TestPrepRecordsList:
    def test_list_prep_records_for_sample(self, client: TestClient) -> None:
        c = client.post("/api/clients", json={"code": "PRC01", "name": "PR Client"}, headers=SUPERVISOR).json()
        p = client.post(
            "/api/projects",
            json={"client_id": c["id"], "name": "PR Project"},
            headers=SUPERVISOR,
        ).json()
        client.post(
            "/api/drill-holes",
            json={"project_id": p["id"], "hole_id": "MSA-26-100"},
            headers=PREP,
        )
        sub = client.post(
            "/api/submissions",
            json={
                "client_id": c["id"],
                "project_id": p["id"],
                "received_at": "2026-01-15T10:00:00",
                "samples": [
                    {"sample_id": "MSA-26-100-142.50_144.00", "sample_type": "core"},
                ],
            },
            headers=PREP,
        ).json()
        sample_id = sub["samples"][0]["id"]
        response = client.get(f"/api/samples/{sample_id}/prep-records", headers=ANALYST)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
