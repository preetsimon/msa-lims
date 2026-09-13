"""Prep record API endpoint tests.

Tests the full request lifecycle: route → service → DB, with real Postgres
and real authentication headers.
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


ANALYST = {"X-Actor": "analyst@lab", "X-Actor-Role": "analyst"}
CLIENT = {"X-Actor": "client@example.com", "X-Actor-Role": "client"}


def _create_sample(client: TestClient) -> int:
    """Create a surface sample through the submissions API, returning its id."""
    import random
    import uuid

    unique = uuid.uuid4().hex[:3].upper()
    serial = random.randint(10000, 99999)
    client_code = f"PR{unique}"

    client_resp = client.post(
        "/api/clients",
        json={"code": client_code, "name": f"Prep Test {unique}"},
        headers={"X-Actor": "manager@lab", "X-Actor-Role": "lab_manager"},
    )
    assert client_resp.status_code == 201
    client_id = client_resp.json()["id"]

    sub_resp = client.post(
        "/api/submissions",
        json={
            "client_id": client_id,
            "received_at": "2026-09-12T10:00:00Z",
            "samples": [
                {
                    "sample_id": f"MSA-24-SO-{serial:05d}",
                    "sample_type": "soil",
                }
            ],
        },
        headers=ANALYST,
    )
    assert sub_resp.status_code == 201, sub_resp.text
    sample_id = sub_resp.json()["samples"][0]["id"]
    return sample_id


def test_create_prep_record(client: TestClient) -> None:
    sample_id = _create_sample(client)

    response = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
            "input_weight_g": "250.0",
            "output_weight_g": "245.0",
        },
        headers=ANALYST,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["sample_id"] == sample_id
    assert data["stage"] == "primary_crush"
    assert data["input_weight_g"] == "250.0"
    assert data["output_weight_g"] == "245.0"
    assert data["id"] > 0
    assert data["supersedes_id"] is None


def test_create_prep_record_insufficient_role(client: TestClient) -> None:
    sample_id = _create_sample(client)

    response = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
        },
        headers=CLIENT,
    )
    assert response.status_code == 403


def test_create_prep_record_sample_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/prep-records",
        json={
            "sample_id": 999999,
            "stage": "primary_crush",
        },
        headers=ANALYST,
    )
    assert response.status_code == 404


def test_create_prep_record_invalid_stage(client: TestClient) -> None:
    sample_id = _create_sample(client)

    response = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "invalid_stage",
        },
        headers=ANALYST,
    )
    assert response.status_code == 422


def test_create_prep_record_output_exceeds_input(client: TestClient) -> None:
    sample_id = _create_sample(client)

    response = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
            "input_weight_g": "100.0",
            "output_weight_g": "150.0",
        },
        headers=ANALYST,
    )
    assert response.status_code == 422


def test_supersede_prep_record(client: TestClient) -> None:
    sample_id = _create_sample(client)

    # Create original record
    create_resp = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
            "input_weight_g": "250.0",
            "output_weight_g": "245.0",
        },
        headers=ANALYST,
    )
    assert create_resp.status_code == 201
    original_id = create_resp.json()["id"]

    # Supersede with correction
    sup_resp = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
            "input_weight_g": "250.0",
            "output_weight_g": "248.0",
            "supersedes_id": original_id,
            "superseded_reason": "Weight was misread on first attempt",
        },
        headers=ANALYST,
    )
    assert sup_resp.status_code == 201
    data = sup_resp.json()
    assert data["supersedes_id"] == original_id
    assert data["superseded_reason"] == "Weight was misread on first attempt"
    assert data["output_weight_g"] == "248.0"


def test_supersede_requires_reason(client: TestClient) -> None:
    sample_id = _create_sample(client)

    # Create original record
    create_resp = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
        },
        headers=ANALYST,
    )
    original_id = create_resp.json()["id"]

    # Supersede without reason — should fail
    sup_resp = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
            "supersedes_id": original_id,
        },
        headers=ANALYST,
    )
    assert sup_resp.status_code == 422


def test_supersede_wrong_sample(client: TestClient) -> None:
    sample_id = _create_sample(client)
    other_sample_id = _create_sample(client)

    # Create record on first sample
    create_resp = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
        },
        headers=ANALYST,
    )
    original_id = create_resp.json()["id"]

    # Try to supersede from a different sample — should fail
    sup_resp = client.post(
        "/api/prep-records",
        json={
            "sample_id": other_sample_id,
            "stage": "primary_crush",
            "supersedes_id": original_id,
            "superseded_reason": "wrong sample",
        },
        headers=ANALYST,
    )
    assert sup_resp.status_code == 422


def test_supersede_nonexistent_record(client: TestClient) -> None:
    sample_id = _create_sample(client)

    response = client.post(
        "/api/prep-records",
        json={
            "sample_id": sample_id,
            "stage": "primary_crush",
            "supersedes_id": 999999,
            "superseded_reason": "does not exist",
        },
        headers=ANALYST,
    )
    assert response.status_code == 422
