"""Client and project registration, through the real FastAPI app.

Same isolation pattern as ``test_submissions_api.py``: the route commits into
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


def create_client_body(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {"code": "MSA", "name": "MSA Test Mining Co"}
    defaults.update(overrides)
    return defaults


class TestCreatingAClient:
    def test_a_manager_registers_a_client(self, client: TestClient) -> None:
        response = client.post("/api/clients", json=create_client_body(), headers=MANAGER)
        assert response.status_code == 201
        body = response.json()
        assert body["code"] == "MSA"
        assert body["is_active"] is True

    def test_an_analyst_is_refused_with_403(self, client: TestClient) -> None:
        response = client.post("/api/clients", json=create_client_body(), headers=ANALYST)
        assert response.status_code == 403

    def test_a_duplicate_code_is_422(self, client: TestClient) -> None:
        client.post("/api/clients", json=create_client_body(), headers=MANAGER)
        response = client.post(
            "/api/clients", json=create_client_body(name="Different Name"), headers=MANAGER
        )
        assert response.status_code == 422

    def test_a_blank_code_is_refused_before_reaching_the_service(self, client: TestClient) -> None:
        response = client.post("/api/clients", json=create_client_body(code=""), headers=MANAGER)
        assert response.status_code == 422


class TestCreatingAProject:
    def test_a_manager_registers_a_project_under_an_existing_client(
        self, client: TestClient
    ) -> None:
        client_id = client.post("/api/clients", json=create_client_body(), headers=MANAGER).json()[
            "id"
        ]

        response = client.post(
            "/api/projects",
            json={"client_id": client_id, "name": "2024 Drill Program", "location": "Red Lake, ON"},
            headers=MANAGER,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["client_id"] == client_id
        assert body["name"] == "2024 Drill Program"

    def test_an_unknown_client_id_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/projects", json={"client_id": 999_999, "name": "Ghost Program"}, headers=MANAGER
        )
        assert response.status_code == 404

    def test_an_analyst_is_refused_with_403(self, client: TestClient) -> None:
        client_id = client.post("/api/clients", json=create_client_body(), headers=MANAGER).json()[
            "id"
        ]

        response = client.post(
            "/api/projects",
            json={"client_id": client_id, "name": "2024 Drill Program"},
            headers=ANALYST,
        )
        assert response.status_code == 403

    def test_the_submission_endpoint_can_now_use_a_registered_project_and_hole(
        self, client: TestClient, session: Session
    ) -> None:
        """The point of building this endpoint: submission intake no longer
        needs a client and project inserted directly through the ORM — only
        the drill hole still does, which PROGRESS.md tracks separately."""
        client_id = client.post("/api/clients", json=create_client_body(), headers=MANAGER).json()[
            "id"
        ]
        project_id = client.post(
            "/api/projects",
            json={"client_id": client_id, "name": "2024 Drill Program"},
            headers=MANAGER,
        ).json()["id"]

        response = client.post(
            "/api/submissions",
            json={
                "client_id": client_id,
                "project_id": project_id,
                "received_at": "2026-08-24T10:00:00Z",
                "samples": [{"sample_id": "MSA-24-SO-00417", "sample_type": "soil"}],
            },
            headers=ANALYST,
        )
        assert response.status_code == 201
