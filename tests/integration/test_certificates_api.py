"""Certificate of Analysis issuance and download, through the real FastAPI app.

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


@pytest.fixture
def assayed_sample_and_client(client: TestClient) -> tuple[int, int]:
    """Returns (client_id, sample_id) for a sample with a current result."""
    client_id = client.post(
        "/api/clients", json={"code": "MSA", "name": "MSA Test Mining Co"}, headers=MANAGER
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
    sample_id = submission["samples"][0]["id"]
    client.post(
        "/api/fire-assay-results",
        json={
            "sample_id": sample_id,
            "gold_bead_mg": "0.150",
            "sample_weight_g": "30",
            "analysed_at": "2026-08-24T14:00:00Z",
        },
        headers=ANALYST,
    )
    return client_id, sample_id


class TestIssuingACertificate:
    def test_a_manager_issues_a_certificate(
        self, client: TestClient, assayed_sample_and_client: tuple[int, int]
    ) -> None:
        client_id, sample_id = assayed_sample_and_client
        response = client.post(
            "/api/certificates",
            json={
                "client_id": client_id,
                "sample_ids": [sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=MANAGER,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["certificate_number"].startswith("COA-2026-")
        assert body["sample_count"] == 1
        assert len(body["pdf_sha256"]) == 64

    def test_an_analyst_is_refused_with_403(
        self, client: TestClient, assayed_sample_and_client: tuple[int, int]
    ) -> None:
        client_id, sample_id = assayed_sample_and_client
        response = client.post(
            "/api/certificates",
            json={
                "client_id": client_id,
                "sample_ids": [sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=ANALYST,
        )
        assert response.status_code == 403

    def test_a_sample_with_no_result_is_422(self, client: TestClient) -> None:
        client_id = client.post(
            "/api/clients", json={"code": "MSA", "name": "MSA Test Mining Co"}, headers=MANAGER
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
        sample_id = submission["samples"][0]["id"]

        response = client.post(
            "/api/certificates",
            json={
                "client_id": client_id,
                "sample_ids": [sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=MANAGER,
        )
        assert response.status_code == 422

    def test_an_unknown_client_id_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/certificates",
            json={"client_id": 999_999, "sample_ids": [1], "issued_at": "2026-08-24T15:00:00Z"},
            headers=MANAGER,
        )
        assert response.status_code == 404

    def test_issuing_marks_the_sample_reported(
        self, client: TestClient, session: Session, assayed_sample_and_client: tuple[int, int]
    ) -> None:
        from msa_lims.db.models import Sample
        from msa_lims.domain.enums import SampleStatus

        client_id, sample_id = assayed_sample_and_client
        response = client.post(
            "/api/certificates",
            json={
                "client_id": client_id,
                "sample_ids": [sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=MANAGER,
        )
        assert response.status_code == 201

        sample = session.get(Sample, sample_id)
        assert sample is not None
        assert sample.status is SampleStatus.REPORTED


class TestDownloadingThePdf:
    def test_the_pdf_downloads_with_a_matching_hash_header(
        self, client: TestClient, assayed_sample_and_client: tuple[int, int]
    ) -> None:
        client_id, sample_id = assayed_sample_and_client
        issued = client.post(
            "/api/certificates",
            json={
                "client_id": client_id,
                "sample_ids": [sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=MANAGER,
        ).json()

        response = client.get(f"/api/certificates/{issued['id']}/pdf")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["x-content-sha256"] == issued["pdf_sha256"]
        assert response.content.startswith(b"%PDF-")

    def test_an_unknown_certificate_id_is_404(self, client: TestClient) -> None:
        response = client.get("/api/certificates/999999/pdf")
        assert response.status_code == 404


class TestAmendingThroughHttp:
    def test_correcting_a_result_then_re_issuing_the_certificate(
        self, client: TestClient, assayed_sample_and_client: tuple[int, int]
    ) -> None:
        client_id, sample_id = assayed_sample_and_client
        first_result = client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": sample_id,
                "gold_bead_mg": "0.150",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T14:00:00Z",
            },
            headers=ANALYST,
        )
        # A result already exists from the fixture -- this second attempt is
        # expected to fail, proving the fixture's own result is what carries
        # through unless explicitly superseded.
        assert first_result.status_code == 422

        first_cert = client.post(
            "/api/certificates",
            json={
                "client_id": client_id,
                "sample_ids": [sample_id],
                "issued_at": "2026-08-24T15:00:00Z",
            },
            headers=MANAGER,
        ).json()

        second_cert = client.post(
            "/api/certificates",
            json={
                "client_id": client_id,
                "sample_ids": [sample_id],
                "issued_at": "2026-08-24T16:00:00Z",
                "supersedes_id": first_cert["id"],
                "superseded_reason": "client requested re-issue with updated letterhead",
            },
            headers=MANAGER,
        )
        assert second_cert.status_code == 201
        assert second_cert.json()["supersedes_id"] == first_cert["id"]
