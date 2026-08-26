"""The sealed QC dossier of a completed batch, through the real FastAPI app.

Covers the whole idea #5 contract: assembly from real furnace rows, the
advisory flags (never verdicts), content-addressed persistence with its
idempotence, the seal recomputing offline, and the refusal shapes — an
unfinished batch is 409, an unknown batch 404, the external role 403.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
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
ANALYST = {"X-Actor": "ana@lab", "X-Actor-Role": "analyst"}
PREP = {"X-Actor": "prep@lab", "X-Actor-Role": "prep_tech"}
CLIENT = {"X-Actor": "geo@mine", "X-Actor-Role": "client"}

RECIPE = {
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


def _walk_to(client: TestClient, batch_id: int, stages: tuple[str, ...]) -> None:
    for stage in stages:
        response = client.patch(
            f"/api/batches/{batch_id}/status", json={"status": stage}, headers=ANALYST
        )
        assert response.status_code == 200, response.text


def _charge(client: TestClient, batch_id: int, body: dict[str, object]) -> int:
    response = client.post(f"/api/batches/{batch_id}/crucibles", json=body, headers=ANALYST)
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _part_and_weigh(client: TestClient, batch_id: int, crucible_id: int, bead: str) -> None:
    parted = client.post(
        f"/api/batches/{batch_id}/crucibles/{crucible_id}/parting",
        json={
            "lead_button_weight_mg": "27.0",
            "prill_weight_mg": "0.500",
            "parting_acid_volume_ml": "5",
            "parted_at": "2026-08-25T13:00:00Z",
        },
        headers=PREP,
    )
    assert parted.status_code == 200, parted.text
    weighed = client.post(
        f"/api/batches/{batch_id}/crucibles/{crucible_id}/weighing",
        json={"gold_bead_mg": bead, "weighed_at": "2026-08-25T13:30:00Z"},
        headers=ANALYST,
    )
    assert weighed.status_code == 200, weighed.text


class CompletedTray:
    """Drive one full batch over HTTP: two sample slots plus a CRM and a blank.

    The CRM's bead is chosen so its grade sits exactly +2 certified
    uncertainties from centre (0.225 mg over 45 g is exactly 5 g/t against
    certified 5.00 ± 0.05). The blank is weighed only when ``blank_bead`` is
    given; ``complete=False`` stops before ``completed``.
    """

    @staticmethod
    def make(
        client: TestClient,
        *,
        crm_bead: str = "0.225",
        blank_bead: str | None = "0.012",
        complete: bool = True,
    ) -> dict[str, int]:
        cid = client.post(
            "/api/clients",
            json={"code": "MSA", "name": "MSA Dossier Mining Co"},
            headers=SUPERVISOR,
        ).json()["id"]
        sub = client.post(
            "/api/submissions",
            json={
                "client_id": cid,
                "received_at": "2026-08-25T10:00:00Z",
                "samples": [
                    {"sample_id": "MSA-24-SO-00901", "sample_type": "soil"},
                    {"sample_id": "MSA-24-SO-00902", "sample_type": "soil"},
                ],
            },
            headers=ANALYST,
        ).json()
        s1, s2 = int(sub["samples"][0]["id"]), int(sub["samples"][1]["id"])

        crm = client.post(
            "/api/qc-materials",
            json={
                "name": "OREAS 501d",
                "qc_type": "crm",
                "certified_au_value_g_t": "4.90",
                "certified_au_uncertainty_g_t": "0.05",
            },
            headers=SUPERVISOR,
        ).json()["id"]
        blank = client.post(
            "/api/qc-materials",
            json={"name": "Silica Sand Blank", "qc_type": "blank"},
            headers=SUPERVISOR,
        ).json()["id"]
        recipe = client.post("/api/flux-recipes", json=RECIPE, headers=SUPERVISOR).json()["id"]

        batch = client.post(
            "/api/batches", json={"opened_at": "2026-08-25T12:00:00Z"}, headers=ANALYST
        ).json()["id"]

        # Both samples walk the real prep path before charging — the lifecycle
        # genuinely gates IN_ASSAY on READY_FOR_ASSAY since Phase 3.
        for sample in (s1, s2):
            walked = client.patch(
                f"/api/samples/{sample}/status",
                json={"target": "in_prep"},
                headers=ANALYST,
            )
            assert walked.status_code == 200, walked.text
            walked = client.patch(
                f"/api/samples/{sample}/status",
                json={"target": "ready_for_assay"},
                headers=ANALYST,
            )
            assert walked.status_code == 200, walked.text

        _walk_to(client, batch, ("charging",))

        ids = {
            "batch": batch,
            "sample_crucible": _charge(
                client,
                batch,
                {
                    "sample_id": s1,
                    "qc_material_id": None,
                    "flux_recipe_id": recipe,
                    "position_row": 1,
                    "position_col": 1,
                    "sample_weight_g": "45",
                    "charged_at": "2026-08-25T12:30:00Z",
                },
            ),
            "crm_crucible": _charge(
                client,
                batch,
                {
                    "sample_id": None,
                    "qc_material_id": crm,
                    "flux_recipe_id": recipe,
                    "position_row": 1,
                    "position_col": 2,
                    "sample_weight_g": "45",
                    "charged_at": "2026-08-25T12:30:00Z",
                },
            ),
            "blank_crucible": _charge(
                client,
                batch,
                {
                    "sample_id": None,
                    "qc_material_id": blank,
                    "flux_recipe_id": recipe,
                    "position_row": 1,
                    "position_col": 3,
                    "sample_weight_g": "30",
                    "charged_at": "2026-08-25T12:30:00Z",
                },
            ),
            "sample2_crucible": _charge(
                client,
                batch,
                {
                    "sample_id": s2,
                    "qc_material_id": None,
                    "flux_recipe_id": recipe,
                    "position_row": 2,
                    "position_col": 1,
                    "sample_weight_g": "30",
                    "charged_at": "2026-08-25T12:35:00Z",
                },
            ),
        }

        _walk_to(client, batch, ("in_fusion", "fused", "in_cupellation", "cupelled"))
        _part_and_weigh(client, batch, ids["crm_crucible"], crm_bead)
        if blank_bead is not None:
            _part_and_weigh(client, batch, ids["blank_crucible"], blank_bead)
        if complete:
            _walk_to(client, batch, ("completed",))
        return ids


def offline_seal(document: dict[str, object]) -> str:
    """Recompute the seal exactly as a recipient would: canonical JSON of
    everything except the seal itself."""
    body = dict(document)
    body.pop("seal")
    rendered = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(rendered.encode()).hexdigest()


def dossier_events(session: Session, batch_id: int) -> int:
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM audit_event WHERE table_name='batch' "
                "AND record_id=:id AND action IN ('create','amend') AND "
                "after ? 'qc_dossier_sha256'"
            ),
            {"id": batch_id},
        ).scalar_one()
    )


class TestQcDossier:
    def test_a_completed_batch_returns_its_qc_evidence_sealed(
        self, client: TestClient, session: Session
    ) -> None:
        ids = CompletedTray.make(client)
        response = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST)
        assert response.status_code == 200
        document = response.json()

        assert len(document["entries"]) == 2
        by_type = {e["material"]["qc_type"]: e for e in document["entries"]}
        crm = by_type["crm"]
        assert Decimal(crm["au"]["value"]) == Decimal("5")  # 0.225 mg / 45 g
        assert crm["portion_g"] == "45"  # derived from the recorded charge
        assert Decimal(crm["z_score"]) == Decimal("2")  # (5 − 4.90) / 0.05
        blank = by_type["blank"]
        assert Decimal(blank["au"]["value"]) == Decimal("0.4")  # 0.012 mg / 30 g
        assert any(a["code"] == "blank_above_threshold" for a in blank["advisories"])

        # The seal recomputes offline from the fetched document alone.
        assert offline_seal(document) == document["seal"]

        # And the pointer landed on the batch row.
        row = session.execute(
            text("SELECT qc_dossier_sha256 FROM batch WHERE id = :id"),
            {"id": ids["batch"]},
        ).one()
        assert row[0] == document["seal"]

    def test_refetch_without_new_facts_is_idempotent(
        self, client: TestClient, session: Session
    ) -> None:
        ids = CompletedTray.make(client)
        first = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST).json()

        blobs_before = session.execute(text("SELECT count(*) FROM stored_blob")).scalar_one()
        events_before = dossier_events(session, ids["batch"])
        assert events_before == 1  # the first fetch wrote exactly one event

        second = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST).json()
        assert second["seal"] == first["seal"]
        assert session.execute(text("SELECT count(*) FROM stored_blob")).scalar_one() == (
            blobs_before
        )
        assert dossier_events(session, ids["batch"]) == events_before

    def test_new_facts_move_the_pointer_and_keep_the_old_evidence(
        self, client: TestClient, session: Session
    ) -> None:
        """The blank was left unweighed; the first dossier flags it. Weighing
        it afterwards is real new evidence — so the second dossier gets a new
        address, one amend event, and the first blob stays on record."""
        ids = CompletedTray.make(client, blank_bead=None)
        first = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST).json()
        blank_entry = next(e for e in first["entries"] if e["material"]["qc_type"] == "blank")
        assert blank_entry["au"] is None
        assert any(a["code"] == "not_yet_weighed" for a in blank_entry["advisories"])

        # The blank crucible is still legitimately CUPELLED: parting and
        # weighing are per-crucible acts, legal after batch completion.
        _part_and_weigh(client, ids["batch"], ids["blank_crucible"], "0.012")

        second = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST).json()
        assert second["seal"] != first["seal"]
        blank_entry_2 = next(e for e in second["entries"] if e["material"]["qc_type"] == "blank")
        assert blank_entry_2["au"] is not None

        blobs = session.execute(text("SELECT count(*) FROM stored_blob")).scalar_one()
        assert blobs == 2  # both generations remain reachable
        events = (
            session.execute(
                text(
                    "SELECT action FROM audit_event WHERE table_name='batch' AND "
                    "record_id=:id AND after ? 'qc_dossier_sha256' ORDER BY id"
                ),
                {"id": ids["batch"]},
            )
            .scalars()
            .all()
        )
        assert list(events) == ["create", "amend"]

    def test_a_batch_that_has_not_completed_is_409(self, client: TestClient) -> None:
        ids = CompletedTray.make(client, complete=False)
        response = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST)
        assert response.status_code == 409
        assert "completed furnace run" in response.json()["detail"]

    def test_an_unknown_batch_is_404(self, client: TestClient) -> None:
        response = client.get("/api/batches/999999/qc-dossier", headers=ANALYST)
        assert response.status_code == 404

    def test_the_external_role_is_refused(self, client: TestClient) -> None:
        ids = CompletedTray.make(client)
        response = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=CLIENT)
        assert response.status_code == 403

    def test_a_batch_without_qc_says_so_instead_of_lying(self, client: TestClient) -> None:
        """Insertion is recorded, not enforced — so zero insertions must read
        as 'no controls ran', never silently as 'reviewed and fine'."""
        cid = client.post(
            "/api/clients",
            json={"code": "MSA", "name": "MSA Dossier Mining Co"},
            headers=SUPERVISOR,
        ).json()["id"]
        sub = client.post(
            "/api/submissions",
            json={
                "client_id": cid,
                "received_at": "2026-08-25T10:00:00Z",
                "samples": [{"sample_id": "MSA-24-SO-00901", "sample_type": "soil"}],
            },
            headers=ANALYST,
        ).json()
        sample = int(sub["samples"][0]["id"])
        for target in ("in_prep", "ready_for_assay"):
            walked = client.patch(
                f"/api/samples/{sample}/status", json={"target": target}, headers=ANALYST
            )
            assert walked.status_code == 200, walked.text
        recipe = client.post("/api/flux-recipes", json=RECIPE, headers=SUPERVISOR).json()["id"]
        batch = client.post(
            "/api/batches", json={"opened_at": "2026-08-25T14:00:00Z"}, headers=ANALYST
        ).json()["id"]
        _walk_to(client, batch, ("charging",))
        _charge(
            client,
            batch,
            {
                "sample_id": int(sub["samples"][0]["id"]),
                "qc_material_id": None,
                "flux_recipe_id": recipe,
                "position_row": 1,
                "position_col": 1,
                "sample_weight_g": "30",
                "charged_at": "2026-08-25T14:30:00Z",
            },
        )
        _walk_to(client, batch, ("in_fusion", "fused", "in_cupellation", "cupelled", "completed"))

        document = client.get(f"/api/batches/{batch}/qc-dossier", headers=ANALYST).json()
        assert document["entries"] == []
        assert any(f["code"] == "no_qc_inserted" for f in document["batch_flags"])

    def test_the_blob_store_stays_append_only_under_the_app_role(
        self, client: TestClient, session: Session, app_engine: Engine
    ) -> None:
        """The grants decision proven where it bites: connected as ``msa_app``,
        Postgres itself refuses to rewrite or drop stored evidence."""
        ids = CompletedTray.make(client)
        client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST)

        address = session.execute(text("SELECT sha256 FROM stored_blob LIMIT 1")).scalar_one()
        raw = app_engine.connect()
        try:
            raw.execute(  # type: ignore[union-attr]
                text("UPDATE stored_blob SET byte_count = 0 WHERE sha256 = :a"),
                {"a": address},
            )
        except Exception as exc:
            assert "permission denied" in str(exc).lower()
        else:
            raise AssertionError("UPDATE on stored_blob should have been refused")
        finally:
            raw.rollback()
            raw.close()


class TestDuplicateInsertions:
    """Duplicates re-insert an already-charged sample; they ride the bench
    paths and surface in the dossier as pair statistics."""

    def _seed(self, client: TestClient) -> dict[str, int]:
        cid = client.post(
            "/api/clients",
            json={"code": "MSA", "name": "MSA Dossier Mining Co"},
            headers=SUPERVISOR,
        ).json()["id"]
        sub = client.post(
            "/api/submissions",
            json={
                "client_id": cid,
                "received_at": "2026-08-25T10:00:00Z",
                "samples": [{"sample_id": "MSA-24-SO-00901", "sample_type": "soil"}],
            },
            headers=ANALYST,
        ).json()
        sample = int(sub["samples"][0]["id"])
        recipe = client.post("/api/flux-recipes", json=RECIPE, headers=SUPERVISOR).json()["id"]
        batch = client.post(
            "/api/batches", json={"opened_at": "2026-08-25T15:00:00Z"}, headers=ANALYST
        ).json()["id"]
        _walk_to(client, batch, ("charging",))
        return {"sample": sample, "recipe": recipe, "batch": batch}

    def _prep(self, client: TestClient, sample: int) -> None:
        for target in ("in_prep", "ready_for_assay"):
            walked = client.patch(
                f"/api/samples/{sample}/status", json={"target": target}, headers=ANALYST
            )
            assert walked.status_code == 200

    def _charge(
        self,
        client: TestClient,
        batch: int,
        *,
        sample: int,
        row: int,
        col: int,
        portion: str,
        insertion_type: str | None = None,
        recipe: int | None = None,
    ) -> object:
        body: dict[str, object] = {
            "sample_id": sample,
            "qc_material_id": None,
            "flux_recipe_id": recipe,
            "position_row": row,
            "position_col": col,
            "sample_weight_g": portion,
            "charged_at": "2026-08-25T15:30:00Z",
        }
        if insertion_type is not None:
            body["insertion_type"] = insertion_type
        return client.post(f"/api/batches/{batch}/crucibles", json=body, headers=ANALYST)

    def test_a_duplicate_requires_its_original_in_assay_first(self, client: TestClient) -> None:
        ids = self._seed(client)
        self._prep(client, ids["sample"])
        response = self._charge(
            client,
            ids["batch"],
            sample=ids["sample"],
            row=2,
            col=1,
            portion="30",
            insertion_type="field_duplicate",
            recipe=ids["recipe"],
        )
        assert response.status_code == 409
        assert "charge its original crucible first" in response.json()["detail"]

    def test_a_duplicate_rides_alongside_its_charged_original(
        self, client: TestClient, session: Session
    ) -> None:
        from msa_lims.db.models import Sample as SampleRow
        from msa_lims.domain.enums import SampleStatus

        ids = self._seed(client)
        self._prep(client, ids["sample"])
        primary = self._charge(
            client,
            ids["batch"],
            sample=ids["sample"],
            row=1,
            col=1,
            portion="30",
            recipe=ids["recipe"],
        )
        assert primary.status_code == 201
        duplicate = self._charge(
            client,
            ids["batch"],
            sample=ids["sample"],
            row=2,
            col=1,
            portion="30",
            insertion_type="field_duplicate",
            recipe=ids["recipe"],
        )
        assert duplicate.status_code == 201
        body = duplicate.json()
        assert body["insertion_type"] == "field_duplicate"
        # The lifecycle belongs to the original's charge alone.
        row = session.get(SampleRow, ids["sample"])
        assert row is not None and row.status is SampleStatus.IN_ASSAY

    def test_insertion_type_with_a_qc_material_is_refused(self, client: TestClient) -> None:
        ids = self._seed(client)
        material = client.post(
            "/api/qc-materials",
            json={"name": "Silica Blank", "qc_type": "blank"},
            headers=SUPERVISOR,
        ).json()["id"]
        response = client.post(
            f"/api/batches/{ids['batch']}/crucibles",
            json={
                "sample_id": None,
                "qc_material_id": material,
                "insertion_type": "field_duplicate",
                "flux_recipe_id": ids["recipe"],
                "position_row": 1,
                "position_col": 9,
                "sample_weight_g": "30",
                "charged_at": "2026-08-25T15:30:00Z",
            },
            headers=ANALYST,
        )
        assert response.status_code == 422

    def test_the_dossier_pairs_a_weighed_duplicate_with_its_original(
        self, client: TestClient
    ) -> None:
        """0.150 mg and 0.090 mg over 30 g portions grade exactly 5 and 3 g/t:
        mean 4, difference 2, RPD exactly 50 % — flagged against the 20 % max."""
        ids = self._seed(client)
        self._prep(client, ids["sample"])
        primary = int(
            self._charge(
                client,
                ids["batch"],
                sample=ids["sample"],
                row=1,
                col=1,
                portion="30",
                recipe=ids["recipe"],
            ).json()["id"]
        )
        duplicate = int(
            self._charge(
                client,
                ids["batch"],
                sample=ids["sample"],
                row=2,
                col=1,
                portion="30",
                insertion_type="field_duplicate",
                recipe=ids["recipe"],
            ).json()["id"]
        )
        _walk_to(client, ids["batch"], ("in_fusion", "fused", "in_cupellation", "cupelled"))
        _part_and_weigh(client, ids["batch"], primary, "0.150")
        _part_and_weigh(client, ids["batch"], duplicate, "0.090")
        _walk_to(client, ids["batch"], ("completed",))

        document = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST).json()
        assert len(document["duplicates"]) == 1
        dup = document["duplicates"][0]
        assert dup["sample"]["label"] == "MSA-24-SO-00901"
        assert Decimal(dup["au"]["value"]) == Decimal("3")  # 0.090 mg / 30 g
        assert Decimal(dup["original_au"]["value"]) == Decimal("5")
        assert Decimal(dup["stats"]["mean_g_t"]) == Decimal("4")
        assert Decimal(dup["stats"]["rpd_percent"]) == Decimal("50")
        assert any(a["code"] == "duplicate_rpd_above_max" for a in dup["advisories"])

        # The seal still recomputes over the whole document including pairs.
        assert offline_seal(document) == document["seal"]

    def test_an_unweighed_duplicate_is_flagged_not_graded(self, client: TestClient) -> None:
        ids = self._seed(client)
        self._prep(client, ids["sample"])
        primary = self._charge(
            client,
            ids["batch"],
            sample=ids["sample"],
            row=1,
            col=1,
            portion="30",
            recipe=ids["recipe"],
        )
        assert primary.status_code == 201
        duplicate = self._charge(
            client,
            ids["batch"],
            sample=ids["sample"],
            row=2,
            col=1,
            portion="30",
            insertion_type="prep_duplicate",
            recipe=ids["recipe"],
        )
        assert duplicate.status_code == 201
        _walk_to(client, ids["batch"], ("in_fusion", "fused", "in_cupellation", "cupelled"))
        _part_and_weigh(client, ids["batch"], int(primary.json()["id"]), "0.150")
        _walk_to(client, ids["batch"], ("completed",))

        document = client.get(f"/api/batches/{ids['batch']}/qc-dossier", headers=ANALYST).json()
        dup = document["duplicates"][0]
        assert dup["au"] is None
        assert any(a["code"] == "not_yet_weighed" for a in dup["advisories"])
        assert dup["stats"] is None

    def test_the_tray_slot_names_its_insertion_type(self, client: TestClient) -> None:
        ids = self._seed(client)
        self._prep(client, ids["sample"])
        primary = self._charge(
            client,
            ids["batch"],
            sample=ids["sample"],
            row=1,
            col=1,
            portion="30",
            recipe=ids["recipe"],
        )
        assert primary.status_code == 201
        duplicate = self._charge(
            client,
            ids["batch"],
            sample=ids["sample"],
            row=2,
            col=1,
            portion="30",
            insertion_type="pulp_duplicate",
            recipe=ids["recipe"],
        )
        assert duplicate.status_code == 201
        detail = client.get(f"/api/batches/{ids['batch']}", headers=ANALYST).json()
        slots = {(s["position_row"], s["position_col"]): s for s in detail["crucibles"]}
        assert slots[(1, 1)]["insertion_type"] is None
        assert slots[(2, 1)]["insertion_type"] == "pulp_duplicate"
