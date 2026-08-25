"""Furnace batching, through the real FastAPI app: open a batch, charge a
crucible, fire it through to completion, entirely over HTTP."""

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
            "samples": [{"sample_id": "MSA-24-SO-00417", "sample_type": "soil"}],
        },
        headers=ANALYST,
    ).json()
    return int(submission["samples"][0]["id"])


@pytest.fixture
def recipe_id(client: TestClient) -> int:
    response = client.post(
        "/api/flux-recipes",
        json={
            "name": "Standard Silicate",
            "matrix_type": "silicate",
            "nominal_portion_g": "30",
            "litharge_g": "60",
            "soda_ash_g": "90",
            "borax_g": "30",
            "silica_g": "15",
            "flour_g": "3",
            "nitre_g": "0",
        },
        headers=SUPERVISOR,
    )
    return int(response.json()["id"])


@pytest.fixture
def batch_id(client: TestClient) -> int:
    response = client.post(
        "/api/batches", json={"opened_at": "2026-08-25T08:00:00Z"}, headers=ANALYST
    )
    return int(response.json()["id"])


class TestOpeningABatch:
    def test_an_analyst_opens_a_batch(self, client: TestClient) -> None:
        response = client.post(
            "/api/batches", json={"opened_at": "2026-08-25T08:00:00Z"}, headers=ANALYST
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "pending"
        assert body["batch_number"].startswith("BATCH-2026-")

    def test_a_client_role_is_refused_with_403(self, client: TestClient) -> None:
        response = client.post(
            "/api/batches", json={"opened_at": "2026-08-25T08:00:00Z"}, headers=FIELD_CLIENT
        )
        assert response.status_code == 403


class TestChargingACrucible:
    def test_charging_is_refused_before_the_batch_is_opened_for_charging(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        response = client.post(
            f"/api/batches/{batch_id}/crucibles",
            json={
                "sample_id": sample_id,
                "flux_recipe_id": recipe_id,
                "position_row": 1,
                "position_col": 1,
                "sample_weight_g": "30",
                "charged_at": "2026-08-25T09:00:00Z",
            },
            headers=ANALYST,
        )
        assert response.status_code == 422

    def test_a_crucible_is_charged_once_the_batch_is_charging(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        client.patch(
            f"/api/batches/{batch_id}/status", json={"status": "charging"}, headers=ANALYST
        )

        response = client.post(
            f"/api/batches/{batch_id}/crucibles",
            json={
                "sample_id": sample_id,
                "flux_recipe_id": recipe_id,
                "position_row": 1,
                "position_col": 1,
                "sample_weight_g": "30",
                "charged_at": "2026-08-25T09:00:00Z",
            },
            headers=ANALYST,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "charged"
        assert body["litharge_g"] == "60"

    def test_a_position_outside_the_configured_tray_is_422(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        client.patch(
            f"/api/batches/{batch_id}/status", json={"status": "charging"}, headers=ANALYST
        )

        response = client.post(
            f"/api/batches/{batch_id}/crucibles",
            json={
                "sample_id": sample_id,
                "flux_recipe_id": recipe_id,
                "position_row": 99,
                "position_col": 1,
                "sample_weight_g": "30",
                "charged_at": "2026-08-25T09:00:00Z",
            },
            headers=ANALYST,
        )
        assert response.status_code == 422


class TestFiringABatchThroughHttp:
    def test_the_full_walk_from_pending_to_completed(
        self, client: TestClient, session: Session, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        client.patch(
            f"/api/batches/{batch_id}/status", json={"status": "charging"}, headers=ANALYST
        )
        client.post(
            f"/api/batches/{batch_id}/crucibles",
            json={
                "sample_id": sample_id,
                "flux_recipe_id": recipe_id,
                "position_row": 1,
                "position_col": 1,
                "sample_weight_g": "30",
                "charged_at": "2026-08-25T09:00:00Z",
            },
            headers=ANALYST,
        )

        for target in ("in_fusion", "fused", "in_cupellation", "cupelled", "completed"):
            response = client.patch(
                f"/api/batches/{batch_id}/status", json={"status": target}, headers=ANALYST
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == target

        from msa_lims.db.models import Sample
        from msa_lims.domain.enums import SampleStatus

        sample = session.get(Sample, sample_id)
        assert sample is not None
        assert sample.status is SampleStatus.IN_ASSAY

    def test_the_batch_detail_lists_its_crucible(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        client.patch(
            f"/api/batches/{batch_id}/status", json={"status": "charging"}, headers=ANALYST
        )
        client.post(
            f"/api/batches/{batch_id}/crucibles",
            json={
                "sample_id": sample_id,
                "flux_recipe_id": recipe_id,
                "position_row": 3,
                "position_col": 4,
                "sample_weight_g": "30",
                "charged_at": "2026-08-25T09:00:00Z",
            },
            headers=ANALYST,
        )

        response = client.get(f"/api/batches/{batch_id}")
        assert response.status_code == 200
        body = response.json()
        assert len(body["crucibles"]) == 1
        assert body["crucibles"][0]["position_row"] == 3

    def test_an_unknown_batch_is_404(self, client: TestClient) -> None:
        response = client.get("/api/batches/999999")
        assert response.status_code == 404
