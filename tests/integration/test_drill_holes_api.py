"""Drill hole registration, through the real FastAPI app.

Same isolation pattern as the other API-level suites: the route commits into
the fixture's outer transaction, which is rolled back afterwards.
"""

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


MANAGER = {"X-Actor": "manager@lab", "X-Actor-Role": "lab_manager"}
ANALYST = {"X-Actor": "analyst@lab", "X-Actor-Role": "analyst"}
FIELD_CLIENT = {"X-Actor": "geo@mineco", "X-Actor-Role": "client"}


@pytest.fixture
def registered_client_id(client: TestClient) -> int:
    return int(
        client.post(
            "/api/clients",
            json={"code": "MSA", "name": "MSA Test Mining Co"},
            headers=MANAGER,
        ).json()["id"]
    )


@pytest.fixture
def project_id(client: TestClient, registered_client_id: int) -> int:
    return int(
        client.post(
            "/api/projects",
            json={"client_id": registered_client_id, "name": "2024 Drill Program"},
            headers=MANAGER,
        ).json()["id"]
    )


class TestCreatingADrillHole:
    def test_an_analyst_registers_a_hole(self, client: TestClient, project_id: int) -> None:
        response = client.post(
            "/api/drill-holes",
            json={
                "project_id": project_id,
                "hole_id": "msa-24-001",
                "easting": "450000",
                "northing": "5510000",
                "total_depth_m": "250",
                "dip_degrees": "-60",
                "azimuth_degrees": "45",
            },
            headers=ANALYST,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["hole_id"] == "MSA-24-001"
        assert body["project_id"] == project_id

    def test_a_client_role_is_refused_with_403(self, client: TestClient, project_id: int) -> None:
        response = client.post(
            "/api/drill-holes",
            json={"project_id": project_id, "hole_id": "MSA-24-001"},
            headers=FIELD_CLIENT,
        )
        assert response.status_code == 403

    def test_an_unknown_project_id_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/drill-holes",
            json={"project_id": 999_999, "hole_id": "MSA-24-001"},
            headers=ANALYST,
        )
        assert response.status_code == 404

    def test_a_malformed_hole_id_is_422(self, client: TestClient, project_id: int) -> None:
        response = client.post(
            "/api/drill-holes",
            json={"project_id": project_id, "hole_id": "not a hole"},
            headers=ANALYST,
        )
        assert response.status_code == 422

    def test_a_duplicate_hole_is_422(self, client: TestClient, project_id: int) -> None:
        client.post(
            "/api/drill-holes",
            json={"project_id": project_id, "hole_id": "MSA-24-001"},
            headers=ANALYST,
        )
        response = client.post(
            "/api/drill-holes",
            json={"project_id": project_id, "hole_id": "msa-24-001"},
            headers=ANALYST,
        )
        assert response.status_code == 422

    def test_an_out_of_range_dip_is_refused_before_reaching_the_service(
        self, client: TestClient, project_id: int
    ) -> None:
        response = client.post(
            "/api/drill-holes",
            json={"project_id": project_id, "hole_id": "MSA-24-001", "dip_degrees": "-91"},
            headers=ANALYST,
        )
        assert response.status_code == 422


class TestTheWholeSpineThroughHttp:
    def test_client_project_hole_and_submission_all_registered_through_the_api(
        self, client: TestClient, registered_client_id: int, project_id: int
    ) -> None:
        """The point of building this endpoint: submission intake no longer
        needs anything inserted directly through the ORM. Client, project,
        drill hole, and finally a drill sample against all three, entirely
        through HTTP."""
        client.post(
            "/api/drill-holes",
            json={"project_id": project_id, "hole_id": "MSA-24-001"},
            headers=ANALYST,
        )

        response = client.post(
            "/api/submissions",
            json={
                "client_id": registered_client_id,
                "project_id": project_id,
                "received_at": "2026-08-24T10:00:00Z",
                "samples": [{"sample_id": "MSA-24-001-142.50_144.00", "sample_type": "core"}],
            },
            headers=ANALYST,
        )
        assert response.status_code == 201
        sample = response.json()["samples"][0]
        assert sample["drill_hole_id"] is not None
        assert sample["from_depth_m"] == "142.50"
