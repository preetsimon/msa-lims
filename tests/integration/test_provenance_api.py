"""The provenance dossier (audit idea #3), through the real FastAPI app.

Mirrors `test_samples_api.py`'s own fixtures and `_charge_into_a_crucible`
helper closely — `samples/service.py` and `provenance/service.py` are the
closest structural analogs in this codebase (pure, read-only assembly over
rows other modules write), and neither has a separate service-level test
file: everything is exercised through the real HTTP chain, since the
"detail view" of either module only means anything once a real chain of
writes exists behind it.
"""

from __future__ import annotations

import hashlib
import json
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
CLIENT_ROLE = {"X-Actor": "client@example.com", "X-Actor-Role": "client"}


def _charge_into_a_crucible(client: TestClient, sample_id: int) -> int:
    """Walks a fresh `RECEIVED` sample through prep and charges it into a
    crucible, entirely through the real endpoints, then walks the batch to
    `cupelled` and parts/weighs the crucible — a fuller version of
    `test_samples_api.py`'s own helper, because the dossier needs the whole
    furnace story, not just enough to unblock result entry. Returns the
    crucible's id.
    """
    client.patch(f"/api/samples/{sample_id}/status", json={"target": "in_prep"}, headers=ANALYST)
    client.patch(
        f"/api/samples/{sample_id}/status", json={"target": "ready_for_assay"}, headers=ANALYST
    )
    recipe = client.post(
        "/api/flux-recipes",
        json={
            "name": f"Provenance Test Recipe {sample_id}",
            "matrix_type": "silicate",
            "nominal_portion_g": "30",
            "litharge_g": "60",
            "soda_ash_g": "90",
            "borax_g": "30",
            "silica_g": "15",
            "flour_g": "3",
            "nitre_g": "0",
        },
        headers=MANAGER,
    ).json()
    batch = client.post(
        "/api/batches", json={"opened_at": "2026-08-25T08:00:00Z"}, headers=ANALYST
    ).json()
    client.patch(f"/api/batches/{batch['id']}/status", json={"status": "charging"}, headers=ANALYST)
    crucible = client.post(
        f"/api/batches/{batch['id']}/crucibles",
        json={
            "sample_id": sample_id,
            "flux_recipe_id": recipe["id"],
            "position_row": 1,
            "position_col": 1,
            "sample_weight_g": "30",
            "charged_at": "2026-08-25T09:00:00Z",
        },
        headers=ANALYST,
    ).json()
    for status in ["in_fusion", "fused", "in_cupellation", "cupelled"]:
        client.patch(f"/api/batches/{batch['id']}/status", json={"status": status}, headers=ANALYST)
    client.post(
        f"/api/batches/{batch['id']}/crucibles/{crucible['id']}/parting",
        json={
            "lead_button_weight_mg": "450",
            "prill_weight_mg": "2.5",
            "parting_acid_volume_ml": "15",
            "parted_at": "2026-08-25T10:00:00Z",
        },
        headers=ANALYST,
    )
    client.post(
        f"/api/batches/{batch['id']}/crucibles/{crucible['id']}/weighing",
        json={"gold_bead_mg": "0.150", "weighed_at": "2026-08-25T10:30:00Z"},
        headers=ANALYST,
    )
    return int(crucible["id"])


@pytest.fixture
def registered_client_id(client: TestClient) -> int:
    return int(
        client.post(
            "/api/clients",
            json={"code": "PRV", "name": "Provenance Test Mining Co"},
            headers=MANAGER,
        ).json()["id"]
    )


@pytest.fixture
def received_sample_id(client: TestClient, registered_client_id: int) -> int:
    submission = client.post(
        "/api/submissions",
        json={
            "client_id": registered_client_id,
            "received_at": "2026-08-24T10:00:00Z",
            "client_reference": "PO-1234",
            "samples": [{"sample_id": "PRV-24-SO-00417", "sample_type": "soil"}],
        },
        headers=ANALYST,
    ).json()
    return int(submission["samples"][0]["id"])


def canonical_sha256(payload: dict[str, object]) -> str:
    """An independent reimplementation — no import from `msa_lims` at all —
    of the exact canonicalisation the backend claims to seal with. If this
    ever silently diverges from `domain/canonical.py`, that is precisely
    the class of bug a seal exists to make loud instead of silent."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestReadingProvenance:
    def test_the_client_role_cannot_read_it(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        response = client.get(f"/api/samples/{received_sample_id}/provenance", headers=CLIENT_ROLE)
        assert response.status_code == 403

    def test_an_unknown_sample_id_is_404(self, client: TestClient) -> None:
        assert client.get("/api/samples/999999/provenance", headers=ANALYST).status_code == 404

    def test_a_freshly_received_sample_has_an_empty_furnace_and_result_history(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        response = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST)
        assert response.status_code == 200
        body = response.json()
        assert body["sample"]["sample_id"] == "PRV-24-SO-00417"
        assert body["crucibles"] == []
        assert body["results"] == []
        assert body["certificates"] == []
        # A sample always has at least "submission received" and "sample
        # logged in" audit events, even with no lab work done yet.
        assert len(body["audit_entries"]) >= 2

    def test_the_submission_and_client_context_is_carried(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        body = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()
        assert body["submission"]["client_reference"] == "PO-1234"
        assert body["client"]["code"] == "PRV"
        assert body["project"] is None
        assert body["drill_hole"] is None

    def test_the_furnace_charge_appears_with_parting_and_weighing(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        _charge_into_a_crucible(client, received_sample_id)
        body = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()
        assert len(body["crucibles"]) == 1
        crucible = body["crucibles"][0]
        assert crucible["position"] == "1-1"
        assert crucible["flux_recipe"] == f"Provenance Test Recipe {received_sample_id}"
        assert crucible["gold_bead_mg"] == "0.150"
        assert crucible["status"] == "weighed"

    def test_a_correction_leaves_both_results_visible_with_the_reason(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        """The exact gap this idea closes: `GET /api/samples/{id}` only ever
        showed the current result. The dossier shows the whole chain."""
        crucible_id = _charge_into_a_crucible(client, received_sample_id)
        first = client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "crucible_id": crucible_id,
                "analysed_at": "2026-08-25T11:00:00Z",
            },
            headers=ANALYST,
        ).json()
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.160",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-25T12:00:00Z",
                "supersedes_id": first["id"],
                "superseded_reason": "transcription error at the balance",
            },
            headers=MANAGER,
        )

        body = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()
        assert [r["id"] for r in body["results"]] == [first["id"], first["id"] + 1]
        assert body["results"][0]["supersedes_id"] is None
        assert body["results"][1]["supersedes_id"] == first["id"]
        assert body["results"][1]["superseded_reason"] == "transcription error at the balance"

    def test_a_certificate_carries_the_specific_result_it_froze(
        self, client: TestClient, registered_client_id: int, received_sample_id: int
    ) -> None:
        crucible_id = _charge_into_a_crucible(client, received_sample_id)
        result = client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "crucible_id": crucible_id,
                "analysed_at": "2026-08-25T11:00:00Z",
            },
            headers=ANALYST,
        ).json()
        certificate = client.post(
            "/api/certificates",
            json={
                "client_id": registered_client_id,
                "sample_ids": [received_sample_id],
                "issued_at": "2026-08-25T12:00:00Z",
            },
            headers=MANAGER,
        ).json()

        body = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()
        assert len(body["certificates"]) == 1
        assert body["certificates"][0]["certificate_number"] == certificate["certificate_number"]
        assert body["certificates"][0]["certified_result_id"] == result["id"]
        assert body["certificates"][0]["pdf_sha256"] == certificate["pdf_sha256"]

    def test_the_audit_entries_grow_with_every_write_in_the_chain(
        self, client: TestClient, registered_client_id: int, received_sample_id: int
    ) -> None:
        before = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()
        before_count = len(before["audit_entries"])

        crucible_id = _charge_into_a_crucible(client, received_sample_id)
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "crucible_id": crucible_id,
                "analysed_at": "2026-08-25T11:00:00Z",
            },
            headers=ANALYST,
        )
        client.post(
            "/api/certificates",
            json={
                "client_id": registered_client_id,
                "sample_ids": [received_sample_id],
                "issued_at": "2026-08-25T12:00:00Z",
            },
            headers=MANAGER,
        )

        after = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()
        assert len(after["audit_entries"]) > before_count
        # Chronological order, not insertion-into-Python order.
        ids = [entry["id"] for entry in after["audit_entries"]]
        assert ids == sorted(ids)
        # Every entry carries its own position in the hash chain (idea #1).
        assert all(len(entry["entry_hash"]) == 64 for entry in after["audit_entries"])


class TestTheSeal:
    def test_is_independently_recomputable_with_no_msa_lims_import(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        body = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()
        claimed = body.pop("seal")
        assert canonical_sha256(body) == claimed

    def test_changes_when_the_underlying_facts_change(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        before = client.get(
            f"/api/samples/{received_sample_id}/provenance", headers=ANALYST
        ).json()["seal"]

        _charge_into_a_crucible(client, received_sample_id)

        after = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()[
            "seal"
        ]
        assert before != after

    def test_two_reads_of_an_unchanged_dossier_seal_identically(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        first = client.get(f"/api/samples/{received_sample_id}/provenance", headers=ANALYST).json()[
            "seal"
        ]
        second = client.get(
            f"/api/samples/{received_sample_id}/provenance", headers=ANALYST
        ).json()["seal"]
        assert first == second
