"""Fire assay result entry, through the real FastAPI app.

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
PREP_TECH = {"X-Actor": "prep@lab", "X-Actor-Role": "prep_tech"}


@pytest.fixture
def sample_id(client: TestClient) -> int:
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
    return int(submission["samples"][0]["id"])


def result_body(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "gold_bead_mg": "0.150",
        "sample_weight_g": "30",
        "analysed_at": "2026-08-24T09:00:00Z",
    }
    defaults.update(overrides)
    return defaults


class TestEnteringAResult:
    def test_an_analyst_enters_a_result(self, client: TestClient, sample_id: int) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id),
            headers=ANALYST,
        )
        assert response.status_code == 201
        body = response.json()
        # 5.000, not 5 -- the domain arithmetic preserves significant figures
        # from the weighing (see domain/values.py: "1.40 g/t says something
        # 1.4 g/t does not"), and that precision survives the round trip
        # through Postgres NUMERIC and back out over the wire.
        assert body["au"] == {
            "value": "5.000",
            "detection_limit": None,
            "censored": False,
            "unit": "g/t",
        }
        assert body["method"] == "fire_assay_gravimetric"

    def test_a_prep_tech_is_refused_with_403(self, client: TestClient, sample_id: int) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id),
            headers=PREP_TECH,
        )
        assert response.status_code == 403

    def test_an_unknown_sample_id_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=999_999),
            headers=ANALYST,
        )
        assert response.status_code == 404

    def test_a_zero_sample_weight_is_422_from_the_domain_calculation(
        self, client: TestClient, sample_id: int
    ) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id, sample_weight_g="0"),
            headers=ANALYST,
        )
        assert response.status_code == 422

    def test_a_second_result_against_the_same_sample_is_422(
        self, client: TestClient, sample_id: int
    ) -> None:
        client.post(
            "/api/fire-assay-results", json=result_body(sample_id=sample_id), headers=ANALYST
        )
        response = client.post(
            "/api/fire-assay-results", json=result_body(sample_id=sample_id), headers=ANALYST
        )
        assert response.status_code == 422

    def test_a_bead_below_sensitivity_renders_as_a_non_detect(
        self, client: TestClient, sample_id: int
    ) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(
                sample_id=sample_id,
                gold_bead_mg="0.0005",
                balance_sensitivity_mg="0.001",
            ),
            headers=ANALYST,
        )
        assert response.status_code == 201
        au = response.json()["au"]
        assert au["censored"] is True
        assert au["value"] is None
        assert au["detection_limit"] is not None


class TestSupersedingThroughHttp:
    def test_correcting_a_result_end_to_end(self, client: TestClient, sample_id: int) -> None:
        first = client.post(
            "/api/fire-assay-results", json=result_body(sample_id=sample_id), headers=ANALYST
        ).json()

        response = client.post(
            "/api/fire-assay-results",
            json=result_body(
                sample_id=sample_id,
                gold_bead_mg="0.160",
                supersedes_id=first["id"],
                superseded_reason="transcription error at the balance",
            ),
            headers=MANAGER,
        )
        assert response.status_code == 201
        second = response.json()
        assert second["supersedes_id"] == first["id"]
        assert second["au"]["value"] != first["au"]["value"]

    def test_superseding_without_a_reason_is_422(self, client: TestClient, sample_id: int) -> None:
        first = client.post(
            "/api/fire-assay-results", json=result_body(sample_id=sample_id), headers=ANALYST
        ).json()

        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id, supersedes_id=first["id"]),
            headers=ANALYST,
        )
        assert response.status_code == 422


class TestTheSampleAdvancesThroughTheSpine:
    def test_the_sample_status_becomes_assayed(
        self, client: TestClient, session: Session, sample_id: int
    ) -> None:
        """The last link in the spine: a submission's sample actually reaches
        ASSAYED once a real result lands, entirely through HTTP -- client,
        submission, and now a computed grade."""
        from msa_lims.db.models import Sample
        from msa_lims.domain.enums import SampleStatus

        response = client.post(
            "/api/fire-assay-results", json=result_body(sample_id=sample_id), headers=ANALYST
        )
        assert response.status_code == 201

        sample = session.get(Sample, sample_id)
        assert sample is not None
        assert sample.status is SampleStatus.ASSAYED


def charge_a_crucible(
    client: TestClient, sample_id: int, *, weight: str = "45", weigh_too: bool = False
) -> int:
    """A crucible charged with this sample and fired to ``CUPELLED`` (or
    walked on to ``WEIGHED``), entirely through the real endpoints -- the
    same walk the live verification does."""
    recipe = client.post(
        "/api/flux-recipes",
        json={
            "name": f"Crucible Wiring {sample_id}",
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
            "position_row": 2,
            "position_col": 3,
            "sample_weight_g": weight,
            "charged_at": "2026-08-25T09:00:00Z",
        },
        headers=ANALYST,
    ).json()
    for stage in ("in_fusion", "fused", "in_cupellation", "cupelled"):
        client.patch(f"/api/batches/{batch['id']}/status", json={"status": stage}, headers=ANALYST)
    if weigh_too:
        client.post(
            f"/api/batches/{batch['id']}/crucibles/{crucible['id']}/parting",
            json={
                "lead_button_weight_mg": "41.2",
                "prill_weight_mg": "0.512",
                "parting_acid_volume_ml": "5",
                "parted_at": "2026-08-25T09:30:00Z",
            },
            headers=ANALYST,
        )
        client.post(
            f"/api/batches/{batch['id']}/crucibles/{crucible['id']}/weighing",
            json={"gold_bead_mg": "0.225", "weighed_at": "2026-08-25T10:00:00Z"},
            headers=ANALYST,
        )
    return int(crucible["id"])


class TestWiringAResultToItsCrucible:
    def test_the_portion_is_derived_from_the_recorded_charge(
        self, client: TestClient, sample_id: int
    ) -> None:
        crucible_id = charge_a_crucible(client, sample_id)
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(
                sample_id=sample_id,
                gold_bead_mg="0.225",
                sample_weight_g=None,
                crucible_id=crucible_id,
            ),
            headers=ANALYST,
        )
        assert response.status_code == 201
        body = response.json()
        # The stored portion is the 45 g charge, not anything re-typed here.
        assert body["crucible_id"] == crucible_id
        assert body["sample_weight_g"] == "45"
        # 0.225 mg from exactly 45 g is exactly 5 g/t.
        assert body["au"]["value"] == "5.000"

    def test_retyping_the_portion_alongside_a_crucible_is_422(
        self, client: TestClient, sample_id: int
    ) -> None:
        crucible_id = charge_a_crucible(client, sample_id)
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id, sample_weight_g="30", crucible_id=crucible_id),
            headers=ANALYST,
        )
        assert response.status_code == 422
        assert "not both" in response.json()["detail"]

    def test_no_weight_and_no_crucible_is_422(self, client: TestClient, sample_id: int) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id, sample_weight_g=None),
            headers=ANALYST,
        )
        assert response.status_code == 422
        assert "required unless" in response.json()["detail"]

    def test_an_unknown_crucible_is_404(self, client: TestClient, sample_id: int) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id, sample_weight_g=None, crucible_id=999_999),
            headers=ANALYST,
        )
        assert response.status_code == 404

    def test_the_weighed_crucible_derives_both_numbers_when_named(
        self, client: TestClient, sample_id: int
    ) -> None:
        """The complete provenance chain over real HTTP: charge 45 g, fire,
        part, weigh 0.225 mg -- then enter a result naming only the crucible.
        Every number on the stored row was recorded at the physical act."""
        crucible_id = charge_a_crucible(client, sample_id, weigh_too=True)
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(
                sample_id=sample_id,
                gold_bead_mg=None,
                sample_weight_g=None,
                crucible_id=crucible_id,
            ),
            headers=ANALYST,
        )
        assert response.status_code == 201
        body = response.json()
        assert body["crucible_id"] == crucible_id
        assert body["sample_weight_g"] == "45"
        assert body["gold_bead_mg"] == "0.225"
        assert body["au"]["value"] == "5.000"

    def test_retyping_the_bead_alongside_a_weighed_crucible_is_422(
        self, client: TestClient, sample_id: int
    ) -> None:
        crucible_id = charge_a_crucible(client, sample_id, weigh_too=True)
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(
                sample_id=sample_id,
                gold_bead_mg="0.300",
                sample_weight_g=None,
                crucible_id=crucible_id,
            ),
            headers=ANALYST,
        )
        assert response.status_code == 422
        assert "already been weighed" in response.json()["detail"]

    def test_direct_entry_with_neither_number_is_422_naming_both_gaps(
        self, client: TestClient, sample_id: int
    ) -> None:
        response = client.post(
            "/api/fire-assay-results",
            json=result_body(sample_id=sample_id, gold_bead_mg=None, sample_weight_g=None),
            headers=ANALYST,
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "gold_bead_mg is required" in detail
        assert "sample_weight_g is required" in detail
