"""Flux recipe registration, through the real FastAPI app."""

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


def recipe_body(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "name": "Standard Silicate",
        "matrix_type": "silicate",
        "nominal_portion_g": "30",
        "litharge_g": "60",
        "soda_ash_g": "90",
        "borax_g": "30",
        "silica_g": "15",
        "flour_g": "3",
        "nitre_g": "0",
    }
    defaults.update(overrides)
    return defaults


class TestRegisteringARecipe:
    def test_a_supervisor_registers_a_recipe(self, client: TestClient) -> None:
        response = client.post("/api/flux-recipes", json=recipe_body(), headers=SUPERVISOR)
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Standard Silicate"
        assert body["matrix_type"] == "silicate"

    def test_an_analyst_is_refused_with_403(self, client: TestClient) -> None:
        response = client.post("/api/flux-recipes", json=recipe_body(), headers=ANALYST)
        assert response.status_code == 403

    def test_a_duplicate_name_is_422(self, client: TestClient) -> None:
        client.post("/api/flux-recipes", json=recipe_body(), headers=SUPERVISOR)
        response = client.post("/api/flux-recipes", json=recipe_body(), headers=SUPERVISOR)
        assert response.status_code == 422

    def test_a_negative_reagent_amount_is_422_before_reaching_the_service(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/flux-recipes", json=recipe_body(litharge_g="-1"), headers=SUPERVISOR
        )
        assert response.status_code == 422
