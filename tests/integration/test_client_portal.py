"""Client portal row-scoping tests.

Verifies that client-role users linked to a Client can only see that
client's samples, and that lab staff retain unrestricted access.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from datetime import UTC, datetime

from msa_lims.db.models import Client, LabUser, Project, Sample, Submission
from msa_lims.domain.enums import Role, SampleStatus, SampleType
from msa_lims.web.app import create_app
from msa_lims.web.deps import get_db

pytestmark = pytest.mark.integration


@pytest.fixture
def app(app_session: Session) -> FastAPI:
    application = create_app()
    application.dependency_overrides[get_db] = lambda: app_session
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def two_clients(app_session: Session) -> tuple[Client, Client]:
    """Two distinct clients with samples."""
    c1 = Client(name="Alpha Mining", code="ALPHA")
    c2 = Client(name="Beta Resources", code="BETA")
    app_session.add_all([c1, c2])
    app_session.flush()

    proj1 = Project(client_id=c1.id, name="ALPHA-001")
    proj2 = Project(client_id=c2.id, name="BETA-001")
    app_session.add_all([proj1, proj2])
    app_session.flush()

    sub1 = Submission(
        client_id=c1.id,
        project_id=proj1.id,
        submission_number="SUB-001",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    sub2 = Submission(
        client_id=c2.id,
        project_id=proj2.id,
        submission_number="SUB-002",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    app_session.add_all([sub1, sub2])
    app_session.flush()

    s1 = Sample(
        submission_id=sub1.id,
        sample_id="MSA-26-001",
        sample_type=SampleType.CORE,
        status=SampleStatus.RECEIVED,
    )
    s2 = Sample(
        submission_id=sub2.id,
        sample_id="MSA-26-002",
        sample_type=SampleType.CORE,
        status=SampleStatus.RECEIVED,
    )
    app_session.add_all([s1, s2])
    app_session.flush()

    # Create lab users: one linked to c1, one linked to c2, one lab staff
    u1 = LabUser(
        subject="sub-client-alpha",
        email="alpha@client.test",
        full_name="Alpha Contact",
        role=Role.CLIENT,
        client_id=c1.id,
    )
    u2 = LabUser(
        subject="sub-client-beta",
        email="beta@client.test",
        full_name="Beta Contact",
        role=Role.CLIENT,
        client_id=c2.id,
    )
    u3 = LabUser(
        subject="sub-lab-staff",
        email="staff@lab.test",
        full_name="Lab Staff",
        role=Role.ANALYST,
        client_id=None,
    )
    app_session.add_all([u1, u2, u3])
    app_session.flush()

    return c1, c2


CLIENT_ALPHA = {
    "X-Actor": "sub-client-alpha",
    "X-Actor-Role": "client",
}
CLIENT_BETA = {
    "X-Actor": "sub-client-beta",
    "X-Actor-Role": "client",
}
LAB_STAFF = {
    "X-Actor": "sub-lab-staff",
    "X-Actor-Role": "analyst",
}


class TestClientPortalSampleScoping:
    def test_client_sees_only_own_samples(
        self, client: TestClient, two_clients: tuple[Client, Client]
    ) -> None:
        response = client.get("/api/samples", headers=CLIENT_ALPHA)
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["client_name"] == "Alpha Mining"

    def test_other_client_cannot_see_samples(
        self, client: TestClient, two_clients: tuple[Client, Client]
    ) -> None:
        response = client.get("/api/samples", headers=CLIENT_BETA)
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        # Beta's sample is different from Alpha's

    def test_lab_staff_sees_all_samples(
        self, client: TestClient, two_clients: tuple[Client, Client]
    ) -> None:
        response = client.get("/api/samples", headers=LAB_STAFF)
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 2

    def test_client_cannot_read_other_clients_sample_detail(
        self, client: TestClient, two_clients: tuple[Client, Client], app_session: Session
    ) -> None:
        # Get Alpha's sample ID
        alpha_resp = client.get("/api/samples", headers=CLIENT_ALPHA)
        alpha_sample_id = alpha_resp.json()["items"][0]["id"]

        # Beta tries to read Alpha's sample
        response = client.get(f"/api/samples/{alpha_sample_id}", headers=CLIENT_BETA)
        assert response.status_code == 404

    def test_client_can_read_own_sample_detail(
        self, client: TestClient, two_clients: tuple[Client, Client], app_session: Session
    ) -> None:
        alpha_resp = client.get("/api/samples", headers=CLIENT_ALPHA)
        alpha_sample_id = alpha_resp.json()["items"][0]["id"]

        response = client.get(f"/api/samples/{alpha_sample_id}", headers=CLIENT_ALPHA)
        assert response.status_code == 200
        assert response.json()["id"] == alpha_sample_id


class TestClientPortalWithoutLink:
    def test_client_without_client_id_is_refused(self, client: TestClient) -> None:
        response = client.get(
            "/api/samples",
            headers={"X-Actor": "unlinked-client", "X-Actor-Role": "client"},
        )
        assert response.status_code == 403
