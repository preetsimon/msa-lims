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


def walk_to_cupelled(client: TestClient, batch_id: int, sample_id: int, recipe_id: int) -> int:
    """Charge one crucible and fire the batch to CUPELLED over real HTTP."""
    client.patch(f"/api/batches/{batch_id}/status", json={"status": "charging"}, headers=ANALYST)
    crucible = client.post(
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
    ).json()
    for stage in ("in_fusion", "fused", "in_cupellation", "cupelled"):
        client.patch(f"/api/batches/{batch_id}/status", json={"status": stage}, headers=ANALYST)
    return int(crucible["id"])


class TestRecordingPartingAndWeighingThroughHttp:
    def part(self, client: TestClient, batch_id: int, crucible_id: int) -> object:
        return client.post(
            f"/api/batches/{batch_id}/crucibles/{crucible_id}/parting",
            json={
                "lead_button_weight_mg": "27.8",
                "prill_weight_mg": "0.512",
                "parting_acid_volume_ml": "5",
                "parted_at": "2026-08-25T10:00:00Z",
            },
            headers=ANALYST,
        )

    def test_parting_then_weighing_walks_the_crucible_to_weighed(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        crucible_id = walk_to_cupelled(client, batch_id, sample_id, recipe_id)

        parted = self.part(client, batch_id, crucible_id)
        assert parted.status_code == 200
        body = parted.json()
        assert body["status"] == "parted"
        assert body["lead_button_weight_mg"] == "27.8"
        assert body["prill_weight_mg"] == "0.512"

        weighed = client.post(
            f"/api/batches/{batch_id}/crucibles/{crucible_id}/weighing",
            json={"gold_bead_mg": "0.225", "weighed_at": "2026-08-25T11:00:00Z"},
            headers=ANALYST,
        )
        assert weighed.status_code == 200
        body = weighed.json()
        assert body["status"] == "weighed"
        assert body["gold_bead_mg"] == "0.225"

    def test_parting_twice_is_409(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        crucible_id = walk_to_cupelled(client, batch_id, sample_id, recipe_id)
        assert self.part(client, batch_id, crucible_id).status_code == 200
        response = self.part(client, batch_id, crucible_id)
        assert response.status_code == 409

    def test_weighing_before_parting_is_409(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        crucible_id = walk_to_cupelled(client, batch_id, sample_id, recipe_id)
        response = client.post(
            f"/api/batches/{batch_id}/crucibles/{crucible_id}/weighing",
            json={"gold_bead_mg": "0.225", "weighed_at": "2026-08-25T11:00:00Z"},
            headers=ANALYST,
        )
        assert response.status_code == 409

    def test_a_negative_measurement_is_422_at_the_schema_layer(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        crucible_id = walk_to_cupelled(client, batch_id, sample_id, recipe_id)
        response = client.post(
            f"/api/batches/{batch_id}/crucibles/{crucible_id}/parting",
            json={
                "lead_button_weight_mg": "-1",
                "prill_weight_mg": "0.512",
                "parting_acid_volume_ml": "5",
                "parted_at": "2026-08-25T10:00:00Z",
            },
            headers=ANALYST,
        )
        assert response.status_code == 422

    def test_a_crucible_from_another_batch_is_404(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        crucible_id = walk_to_cupelled(client, batch_id, sample_id, recipe_id)
        other_batch = client.post(
            "/api/batches", json={"opened_at": "2026-08-25T12:00:00Z"}, headers=ANALYST
        ).json()
        response = self.part(client, other_batch["id"], crucible_id)
        assert response.status_code == 404

    def test_a_client_role_is_refused_with_403(
        self, client: TestClient, batch_id: int, sample_id: int, recipe_id: int
    ) -> None:
        crucible_id = walk_to_cupelled(client, batch_id, sample_id, recipe_id)
        response = client.post(
            f"/api/batches/{batch_id}/crucibles/{crucible_id}/parting",
            json={
                "lead_button_weight_mg": "27.8",
                "prill_weight_mg": "0.512",
                "parting_acid_volume_ml": "5",
                "parted_at": "2026-08-25T10:00:00Z",
            },
            headers=FIELD_CLIENT,
        )
        assert response.status_code == 403
