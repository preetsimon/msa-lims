"""Bare sample lifecycle moves, through the real FastAPI app."""

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


PREP_TECH = {"X-Actor": "prep@lab", "X-Actor-Role": "prep_tech"}
SUPERVISOR = {"X-Actor": "sup@lab", "X-Actor-Role": "supervisor"}
FIELD_CLIENT = {"X-Actor": "geo@mineco", "X-Actor-Role": "client"}


@pytest.fixture
def sample_id(client: TestClient) -> int:
    client_id = client.post(
        "/api/clients", json={"code": "MSA", "name": "MSA Test Mining Co"}, headers=SUPERVISOR
    ).json()["id"]
    submission = client.post(
        "/api/submissions",
        json={
            "client_id": client_id,
            "received_at": "2026-08-24T10:00:00Z",
            "samples": [{"sample_id": "MSA-24-SO-00001", "sample_type": "soil"}],
        },
        headers=SUPERVISOR,
    ).json()
    return int(submission["samples"][0]["id"])


class TestThePrepWalk:
    def test_a_soil_sample_walks_from_received_to_ready_for_assay(
        self, client: TestClient, sample_id: int
    ) -> None:
        start = client.patch(
            f"/api/samples/{sample_id}/status", json={"target": "in_prep"}, headers=PREP_TECH
        )
        assert start.status_code == 200
        assert start.json()["status"] == "in_prep"

        finish = client.patch(
            f"/api/samples/{sample_id}/status",
            json={"target": "ready_for_assay"},
            headers=PREP_TECH,
        )
        assert finish.status_code == 200
        assert finish.json()["status"] == "ready_for_assay"

    def test_a_soil_sample_may_not_skip_preparation(
        self, client: TestClient, sample_id: int
    ) -> None:
        response = client.patch(
            f"/api/samples/{sample_id}/status",
            json={"target": "ready_for_assay"},
            headers=PREP_TECH,
        )
        assert response.status_code == 409

    def test_a_client_role_is_refused_with_403(self, client: TestClient, sample_id: int) -> None:
        response = client.patch(
            f"/api/samples/{sample_id}/status", json={"target": "in_prep"}, headers=FIELD_CLIENT
        )
        assert response.status_code == 403

    def test_an_unknown_sample_is_404(self, client: TestClient) -> None:
        response = client.patch(
            "/api/samples/999999/status", json={"target": "in_prep"}, headers=PREP_TECH
        )
        assert response.status_code == 404

    def test_in_assay_is_refused_before_it_reaches_the_service(
        self, client: TestClient, sample_id: int
    ) -> None:
        """``in_assay`` is only ever reached by charging a crucible — this
        endpoint refuses it at the schema layer, not the service."""
        response = client.patch(
            f"/api/samples/{sample_id}/status", json={"target": "in_assay"}, headers=PREP_TECH
        )
        assert response.status_code == 422


class TestRejection:
    def test_a_supervisor_rejects_received_material_with_a_reason(
        self, client: TestClient, sample_id: int
    ) -> None:
        response = client.patch(
            f"/api/samples/{sample_id}/status",
            json={"target": "rejected", "reason": "bag split in transit"},
            headers=SUPERVISOR,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

    def test_rejection_without_a_reason_is_422(self, client: TestClient, sample_id: int) -> None:
        response = client.patch(
            f"/api/samples/{sample_id}/status", json={"target": "rejected"}, headers=SUPERVISOR
        )
        assert response.status_code == 422
