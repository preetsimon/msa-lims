"""Sample lookup, through the real FastAPI app.

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
CLIENT = {"X-Actor": "client@example.com", "X-Actor-Role": "client"}


@pytest.fixture
def registered_client_id(client: TestClient) -> int:
    return int(
        client.post(
            "/api/clients", json={"code": "MSA", "name": "MSA Test Mining Co"}, headers=MANAGER
        ).json()["id"]
    )


@pytest.fixture
def received_sample_id(client: TestClient, registered_client_id: int) -> int:
    submission = client.post(
        "/api/submissions",
        json={
            "client_id": registered_client_id,
            "received_at": "2026-08-24T10:00:00Z",
            "samples": [{"sample_id": "MSA-24-SO-00417", "sample_type": "soil"}],
        },
        headers=ANALYST,
    ).json()
    return int(submission["samples"][0]["id"])


class TestReadingASample:
    def test_the_client_role_cannot_browse_lab_records(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        """There is no LabUser↔Client link to scope rows by yet, so an
        open read would let any client account read any other client's
        grades by id. Until per-client scoping exists, the external role is
        refused outright."""
        listing = client.get("/api/samples", headers=CLIENT)
        assert listing.status_code == 403
        detail = client.get(f"/api/samples/{received_sample_id}", headers=CLIENT)
        assert detail.status_code == 403

    def test_an_internal_role_can_still_read(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        assert client.get("/api/samples", headers=ANALYST).status_code == 200
        assert client.get(f"/api/samples/{received_sample_id}", headers=ANALYST).status_code == 200

    def test_a_freshly_received_sample_has_no_result_and_no_certificates(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        response = client.get(f"/api/samples/{received_sample_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["sample_id"] == "MSA-24-SO-00417"
        assert body["status"] == "received"
        assert body["current_result"] is None
        assert body["certificates"] == []

    def test_an_unknown_sample_id_is_404(self, client: TestClient) -> None:
        response = client.get("/api/samples/999999")
        assert response.status_code == 404

    def test_after_a_result_the_sample_shows_its_current_grade(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.150",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T14:00:00Z",
            },
            headers=ANALYST,
        )

        response = client.get(f"/api/samples/{received_sample_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "assayed"
        assert body["current_result"] is not None
        assert body["current_result"]["au"]["value"] == "5.000"

    def test_after_a_correction_the_sample_shows_the_current_not_the_original(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        first = client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.150",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T14:00:00Z",
            },
            headers=ANALYST,
        ).json()
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.200",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T15:00:00Z",
                "supersedes_id": first["id"],
                "superseded_reason": "re-weighed",
            },
            headers=ANALYST,
        )

        response = client.get(f"/api/samples/{received_sample_id}")
        body = response.json()
        assert body["current_result"]["id"] != first["id"]
        assert body["current_result"]["au"]["value"] != first["au"]["value"]

    def test_after_a_certificate_the_sample_lists_it_and_is_reported(
        self, client: TestClient, registered_client_id: int, received_sample_id: int
    ) -> None:
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.150",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T14:00:00Z",
            },
            headers=ANALYST,
        )
        certificate = client.post(
            "/api/certificates",
            json={
                "client_id": registered_client_id,
                "sample_ids": [received_sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=MANAGER,
        ).json()

        response = client.get(f"/api/samples/{received_sample_id}")
        body = response.json()
        assert body["status"] == "reported"
        assert body["certificates"] == [
            {"id": certificate["id"], "certificate_number": certificate["certificate_number"]}
        ]

    def test_a_superseded_certificate_still_appears_in_the_list(
        self, client: TestClient, registered_client_id: int, received_sample_id: int
    ) -> None:
        """Every certificate that ever named this sample is listed, not just
        the current one -- the sample detail view is a history, not a
        pointer to the latest document."""
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.150",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T14:00:00Z",
            },
            headers=ANALYST,
        )
        first_cert = client.post(
            "/api/certificates",
            json={
                "client_id": registered_client_id,
                "sample_ids": [received_sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=MANAGER,
        ).json()
        second_cert = client.post(
            "/api/certificates",
            json={
                "client_id": registered_client_id,
                "sample_ids": [received_sample_id],
                "issued_at": "2026-08-24T16:00:00Z",
                "supersedes_id": first_cert["id"],
                "superseded_reason": "re-issued",
            },
            headers=MANAGER,
        ).json()

        response = client.get(f"/api/samples/{received_sample_id}")
        ids = {c["id"] for c in response.json()["certificates"]}
        assert ids == {first_cert["id"], second_cert["id"]}


class TestListingSamples:
    def test_an_empty_lab_lists_nothing(self, client: TestClient) -> None:
        response = client.get("/api/samples")
        assert response.status_code == 200
        assert response.json() == []

    def test_a_received_sample_appears_with_its_client_and_submission(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        response = client.get("/api/samples")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        row = body[0]
        assert row["id"] == received_sample_id
        assert row["sample_id"] == "MSA-24-SO-00417"
        assert row["status"] == "received"
        assert row["client_name"] == "MSA Test Mining Co"
        assert row["submission_number"].startswith("SUB-2026-")

    def test_the_list_does_not_include_the_grade_or_certificates(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        """Deliberately lean: the list is one query, the detail view carries
        the rest."""
        row = client.get("/api/samples").json()[0]
        assert "current_result" not in row
        assert "certificates" not in row

    def test_newest_sample_first(self, client: TestClient, registered_client_id: int) -> None:
        for label in ("MSA-24-SO-00001", "MSA-24-SO-00002"):
            client.post(
                "/api/submissions",
                json={
                    "client_id": registered_client_id,
                    "received_at": "2026-08-24T10:00:00Z",
                    "samples": [{"sample_id": label, "sample_type": "soil"}],
                },
                headers=ANALYST,
            )

        body = client.get("/api/samples").json()
        assert [row["sample_id"] for row in body] == ["MSA-24-SO-00002", "MSA-24-SO-00001"]

    def test_filtering_by_status(
        self, client: TestClient, registered_client_id: int, received_sample_id: int
    ) -> None:
        client.post(
            "/api/submissions",
            json={
                "client_id": registered_client_id,
                "received_at": "2026-08-24T10:00:00Z",
                "samples": [{"sample_id": "MSA-24-SO-00099", "sample_type": "soil"}],
            },
            headers=ANALYST,
        )
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.150",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T14:00:00Z",
            },
            headers=ANALYST,
        )

        assayed = client.get("/api/samples", params={"status": "assayed"}).json()
        assert [row["id"] for row in assayed] == [received_sample_id]

        received = client.get("/api/samples", params={"status": "received"}).json()
        assert [row["sample_id"] for row in received] == ["MSA-24-SO-00099"]

    def test_filtering_by_client(self, client: TestClient, registered_client_id: int) -> None:
        other_client_id = client.post(
            "/api/clients", json={"code": "OTH", "name": "Other Mining Co"}, headers=MANAGER
        ).json()["id"]
        client.post(
            "/api/submissions",
            json={
                "client_id": other_client_id,
                "received_at": "2026-08-24T10:00:00Z",
                "samples": [{"sample_id": "OTH-24-SO-00001", "sample_type": "soil"}],
            },
            headers=ANALYST,
        )
        client.post(
            "/api/submissions",
            json={
                "client_id": registered_client_id,
                "received_at": "2026-08-24T10:00:00Z",
                "samples": [{"sample_id": "MSA-24-SO-00001", "sample_type": "soil"}],
            },
            headers=ANALYST,
        )

        body = client.get("/api/samples", params={"client_id": registered_client_id}).json()
        assert [row["sample_id"] for row in body] == ["MSA-24-SO-00001"]

    def test_an_invalid_status_value_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/samples", params={"status": "not-a-real-status"})
        assert response.status_code == 422
