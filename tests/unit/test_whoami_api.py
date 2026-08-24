"""The auth dependency exercised through the real app, not called directly.

`/api/me` exists for exactly this: a test (or a developer) can hit it and see
who the dependency chain thinks they are, under the same wiring every write
endpoint from Phase 1 onward will use.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from msa_lims.config import get_settings
from msa_lims.web.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


class TestDevHeaderMode:
    """The default mode in local and CI — MSA_ENV defaults to 'local'."""

    def test_with_no_headers_the_actor_defaults_to_the_least_privileged_role(
        self, client: TestClient
    ) -> None:
        # Forgetting the header must not accidentally grant certificate-signing
        # power.
        response = client.get("/api/me")
        assert response.status_code == 200
        assert response.json() == {"name": "dev@localhost", "role": "analyst"}

    def test_headers_set_the_actor(self, client: TestClient) -> None:
        response = client.get(
            "/api/me",
            headers={"X-Actor": "priya@lab", "X-Actor-Role": "lab_manager"},
        )
        assert response.status_code == 200
        assert response.json() == {"name": "priya@lab", "role": "lab_manager"}

    def test_an_unknown_role_is_refused_with_the_valid_list(self, client: TestClient) -> None:
        response = client.get("/api/me", headers={"X-Actor-Role": "chief-wizard"})
        assert response.status_code == 400
        assert "lab_manager" in response.json()["detail"]


class TestOidcModeWithoutHeadersIsRefused:
    def test_a_bare_request_gets_401_not_a_default_actor(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSA_AUTH_MODE", "oidc")
        get_settings.cache_clear()
        try:
            response = client.get("/api/me")
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
        finally:
            get_settings.cache_clear()

    def test_a_malformed_scheme_is_also_401(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MSA_AUTH_MODE", "oidc")
        get_settings.cache_clear()
        try:
            response = client.get("/api/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
            assert response.status_code == 401
        finally:
            get_settings.cache_clear()


class TestDevHeadersAreRefusedOutsideLocalAndCi:
    def test_a_staging_deployment_refuses_the_dev_shim(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The guardrail Sentinel's audit exists to find: a development shortcut
        # that quietly kept working somewhere it must not.
        monkeypatch.setenv("MSA_ENV", "staging")
        get_settings.cache_clear()
        try:
            response = client.get(
                "/api/me", headers={"X-Actor": "attacker", "X-Actor-Role": "lab_manager"}
            )
            assert response.status_code == 501
            assert "MSA_AUTH_MODE=oidc" in response.json()["detail"]
        finally:
            get_settings.cache_clear()
