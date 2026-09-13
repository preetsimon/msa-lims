"""Multi-element ICP HTTP API tests.

Exercises the POST /import, GET /, and PATCH / endpoints against a real
database-backed TestClient.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from msa_lims.db.models import Client, Sample, Submission
from msa_lims.domain.enums import (
    SampleStatus,
    SampleType,
)
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


ANALYST = {"X-Actor": "mer-analyst@lab", "X-Actor-Role": "analyst"}


@pytest.fixture
def in_assay_sample(session: Session) -> Sample:
    """A sample already at IN_ASSAY status — ready for multi-element import."""
    client = Client(code="MSA", name="MSA Test Mining Co")
    session.add(client)
    session.flush()

    submission = Submission(
        submission_number="SUB-2026-9301",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    session.add(submission)
    session.flush()

    sample = Sample(
        sample_id="MSA-24-SO-00701",
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        status=SampleStatus.IN_ASSAY,
    )
    session.add(sample)
    session.flush()
    return sample


@pytest.fixture
def received_sample(session: Session) -> Sample:
    """A sample at RECEIVED status — should be refused by import."""
    client = Client(code="RCV", name="Receive Client")
    session.add(client)
    session.flush()

    submission = Submission(
        submission_number="SUB-2026-9302",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    session.add(submission)
    session.flush()

    sample = Sample(
        sample_id="MSA-24-SO-00702",
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        status=SampleStatus.RECEIVED,
    )
    session.add(sample)
    session.flush()
    return sample


def _import_body(sample_id: int, **overrides: object) -> dict:
    defaults = {
        "sample_id": sample_id,
        "digest_method": "aqua_regia",
        "analysed_at": "2026-08-24T09:00:00Z",
        "results": [
            {
                "element": "Cu",
                "grade_value": "120.5",
                "grade_unit": "ppm",
                "detection_limit": "0.5",
            },
        ],
    }
    defaults.update(overrides)
    return defaults


class TestImportEndpoint:
    def test_post_import_returns_201_and_results(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        resp = client.post(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            json=_import_body(in_assay_sample.id),
            headers=ANALYST,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["sample_id"] == in_assay_sample.id
        assert data["digest_method"] == "aqua_regia"
        assert len(data["imported"]) == 1
        assert data["imported"][0]["element"] == "Cu"
        assert data["imported"][0]["grade_value"] == "120.5"
        assert data["imported"][0]["grade_censored"] is False

    def test_post_import_with_censored_result(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        resp = client.post(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            json=_import_body(
                in_assay_sample.id,
                results=[
                    {
                        "element": "As",
                        "grade_value": "0.002",
                        "grade_unit": "ppm",
                        "detection_limit": "0.010",
                    },
                ],
            ),
            headers=ANALYST,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["imported"][0]["grade_censored"] is True
        assert data["imported"][0]["grade_value"] == "0.010"

    def test_post_import_rejects_concentration_unit(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        resp = client.post(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            json=_import_body(
                in_assay_sample.id,
                results=[
                    {"element": "Cu", "grade_value": "100", "grade_unit": "mg/L"},
                ],
            ),
            headers=ANALYST,
        )
        assert resp.status_code == 422, resp.text

    def test_post_import_refuses_received_sample(
        self, client: TestClient, received_sample: Sample
    ) -> None:
        resp = client.post(
            f"/api/samples/{received_sample.id}/multi-element-results",
            json=_import_body(received_sample.id),
            headers=ANALYST,
        )
        assert resp.status_code in (409, 422), resp.text

    def test_post_import_unknown_sample_returns_404(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/samples/999999/multi-element-results",
            json=_import_body(999999),
            headers=ANALYST,
        )
        assert resp.status_code == 404, resp.text

    def test_post_import_invalid_element_returns_422(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        resp = client.post(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            json=_import_body(
                in_assay_sample.id,
                results=[
                    {"element": "Xx", "grade_value": "100", "grade_unit": "ppm"},
                ],
            ),
            headers=ANALYST,
        )
        assert resp.status_code == 422, resp.text


class TestListEndpoint:
    def test_get_results_after_import(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        client.post(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            json=_import_body(in_assay_sample.id),
            headers=ANALYST,
        )

        resp = client.get(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            headers=ANALYST,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 1
        assert data[0]["element"] == "Cu"
        assert data[0]["grade_censored"] is False

    def test_get_results_returns_empty_for_no_results(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        resp = client.get(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            headers=ANALYST,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == []


class TestSupersedeEndpoint:
    def test_patch_supersedes_existing_result(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        import_resp = client.post(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            json=_import_body(in_assay_sample.id),
            headers=ANALYST,
        )
        assert import_resp.status_code == 201

        resp = client.patch(
            f"/api/samples/{in_assay_sample.id}/multi-element-results/Cu",
            json={
                "digest_method": "aqua_regia",
                "grade_value": "125.0",
                "grade_unit": "ppm",
                "analysed_at": "2026-08-25T10:00:00Z",
                "reason": "calibration update",
            },
            headers=ANALYST,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["grade_value"] == "125.0"
        assert data["supersedes_id"] is not None

    def test_patch_with_censored_reading(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        client.post(
            f"/api/samples/{in_assay_sample.id}/multi-element-results",
            json=_import_body(in_assay_sample.id),
            headers=ANALYST,
        )

        resp = client.patch(
            f"/api/samples/{in_assay_sample.id}/multi-element-results/Cu",
            json={
                "digest_method": "aqua_regia",
                "grade_value": "0.002",
                "grade_unit": "ppm",
                "detection_limit": "0.010",
                "analysed_at": "2026-08-25T10:00:00Z",
                "reason": "below detection limit",
            },
            headers=ANALYST,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["grade_censored"] is True
        assert data["grade_value"] == "0.010"

    def test_patch_invalid_element_returns_422(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        resp = client.patch(
            f"/api/samples/{in_assay_sample.id}/multi-element-results/Xx",
            json={
                "digest_method": "aqua_regia",
                "grade_value": "100",
                "grade_unit": "ppm",
                "analysed_at": "2026-08-25T10:00:00Z",
                "reason": "test",
            },
            headers=ANALYST,
        )
        assert resp.status_code == 422, resp.text

    def test_patch_no_result_to_supersede_returns_422(
        self, client: TestClient, in_assay_sample: Sample
    ) -> None:
        resp = client.patch(
            f"/api/samples/{in_assay_sample.id}/multi-element-results/Cu",
            json={
                "digest_method": "aqua_regia",
                "grade_value": "100",
                "grade_unit": "ppm",
                "analysed_at": "2026-08-25T10:00:00Z",
                "reason": "no current result",
            },
            headers=ANALYST,
        )
        assert resp.status_code == 422, resp.text
        assert "nothing to supersede" in resp.json()["detail"]
