"""The submission API, through a real FastAPI app and a real Postgres session.

The session override is the interesting part: the route commits, but that
commit lands inside the fixture's outer transaction, which is rolled back
afterwards. So the route's real behaviour — including the LabUser
lookup-or-provision that authentication feeds into — is exercised honestly
while the database is left clean.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from msa_lims.db.models import Client, DrillHole, LabUser, Project
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


@pytest.fixture
def a_client(session: Session) -> Client:
    client = Client(code="MSA", name="MSA Test Mining Co")
    session.add(client)
    session.flush()
    return client


@pytest.fixture
def a_project(session: Session, a_client: Client) -> Project:
    project = Project(client_id=a_client.id, name="2024 Drill Program")
    session.add(project)
    session.flush()
    return project


@pytest.fixture
def a_hole(session: Session, a_project: Project) -> DrillHole:
    hole = DrillHole(project_id=a_project.id, hole_id="MSA-24-001")
    session.add(hole)
    session.flush()
    return hole


def submit(client: TestClient, body: dict[str, object], **headers: str) -> object:
    return client.post("/api/submissions", json=body, headers=headers)


def a_body(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "client_id": 1,
        "received_at": datetime(2026, 8, 24, tzinfo=UTC).isoformat(),
        "samples": [{"sample_id": "MSA-24-SO-00417", "sample_type": "soil"}],
    }
    defaults.update(overrides)
    return defaults


class TestCreatingASubmission:
    def test_a_surface_sample_is_received(self, client: TestClient, a_client: Client) -> None:
        response = submit(
            client,
            a_body(client_id=a_client.id),
            **{"X-Actor": "priya@lab", "X-Actor-Role": "analyst"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["submission_number"].startswith("SUB-2026-")
        assert len(body["samples"]) == 1
        assert body["samples"][0]["status"] == "received"

    def test_a_drill_sample_end_to_end(
        self, client: TestClient, a_client: Client, a_project: Project, a_hole: DrillHole
    ) -> None:
        response = submit(
            client,
            a_body(
                client_id=a_client.id,
                project_id=a_project.id,
                samples=[{"sample_id": "MSA-24-001-142.50_144.00", "sample_type": "core"}],
            ),
            **{"X-Actor": "priya@lab", "X-Actor-Role": "analyst"},
        )
        assert response.status_code == 201
        sample = response.json()["samples"][0]
        assert sample["from_depth_m"] == "142.50"
        assert sample["to_depth_m"] == "144.00"

    def test_the_default_dev_actor_may_create_a_submission(
        self, client: TestClient, a_client: Client
    ) -> None:
        # No headers at all -> analyst, per the same default proven in
        # test_whoami_api.py -- and analyst is a bench role, so this succeeds.
        response = submit(client, a_body(client_id=a_client.id))
        assert response.status_code == 201

    def test_a_client_role_is_refused_with_403(self, client: TestClient, a_client: Client) -> None:
        response = submit(
            client,
            a_body(client_id=a_client.id),
            **{"X-Actor": "geologist@mineco", "X-Actor-Role": "client"},
        )
        assert response.status_code == 403

    def test_an_unknown_client_id_is_404(self, client: TestClient) -> None:
        response = submit(client, a_body(client_id=999_999))
        assert response.status_code == 404

    def test_validation_problems_come_back_as_422(
        self, client: TestClient, a_client: Client
    ) -> None:
        response = submit(
            client,
            a_body(
                client_id=a_client.id, samples=[{"sample_id": "not a label", "sample_type": "soil"}]
            ),
        )
        assert response.status_code == 422

    def test_a_malformed_body_is_refused_before_it_reaches_the_service(
        self, client: TestClient, a_client: Client
    ) -> None:
        # No samples at all -- Field(min_length=1) on SubmissionCreate.samples.
        response = submit(client, a_body(client_id=a_client.id, samples=[]))
        assert response.status_code == 422


class TestLabUserProvisioning:
    def test_a_new_actor_gets_a_lab_user_row_on_first_write(
        self, client: TestClient, session: Session, a_client: Client
    ) -> None:
        assert session.scalar(select(LabUser).where(LabUser.subject == "new-tech@lab")) is None

        response = submit(
            client,
            a_body(
                client_id=a_client.id,
                samples=[{"sample_id": "MSA-24-SO-00001", "sample_type": "soil"}],
            ),
            **{"X-Actor": "new-tech@lab", "X-Actor-Role": "prep_tech"},
        )
        assert response.status_code == 201

        user = session.scalar(select(LabUser).where(LabUser.subject == "new-tech@lab"))
        assert user is not None
        assert user.role.value == "prep_tech"
        assert user.full_name == "new-tech@lab"

    def test_the_submission_records_who_received_it(
        self, client: TestClient, session: Session, a_client: Client
    ) -> None:
        response = submit(
            client,
            a_body(
                client_id=a_client.id,
                samples=[{"sample_id": "MSA-24-SO-00002", "sample_type": "soil"}],
            ),
            **{"X-Actor": "front-desk@lab", "X-Actor-Role": "prep_tech"},
        )
        assert response.status_code == 201

        from msa_lims.db.models import Submission

        submission = session.scalar(
            select(Submission).where(Submission.id == response.json()["id"])
        )
        assert submission is not None
        assert submission.received_by_id is not None
        received_by = session.get(LabUser, submission.received_by_id)
        assert received_by is not None
        assert received_by.subject == "front-desk@lab"

    def test_a_returning_actor_reuses_the_same_row(
        self, client: TestClient, session: Session, a_client: Client
    ) -> None:
        submit(
            client,
            a_body(
                client_id=a_client.id,
                samples=[{"sample_id": "MSA-24-SO-00003", "sample_type": "soil"}],
            ),
            **{"X-Actor": "repeat@lab", "X-Actor-Role": "analyst"},
        )
        submit(
            client,
            a_body(
                client_id=a_client.id,
                samples=[{"sample_id": "MSA-24-SO-00004", "sample_type": "soil"}],
            ),
            **{"X-Actor": "repeat@lab", "X-Actor-Role": "analyst"},
        )
        users = session.scalars(select(LabUser).where(LabUser.subject == "repeat@lab")).all()
        assert len(users) == 1
