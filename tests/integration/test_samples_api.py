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


def _charge_into_a_crucible(client: TestClient, sample_id: int) -> int:
    """Walks a fresh ``RECEIVED`` sample through prep and charges it into a
    crucible, entirely through the real endpoints — entering a result now
    requires the sample to genuinely be ``IN_ASSAY`` (see
    ``fire_assay_results/service.py``'s module docstring), which only
    charging produces. ``received_sample_id`` itself stays uncharged, since
    some tests in this file specifically assert on a sample that is still
    genuinely ``RECEIVED``."""
    client.patch(f"/api/samples/{sample_id}/status", json={"target": "in_prep"}, headers=ANALYST)
    client.patch(
        f"/api/samples/{sample_id}/status", json={"target": "ready_for_assay"}, headers=ANALYST
    )
    recipe = client.post(
        "/api/flux-recipes",
        json={
            "name": f"Samples API Test Recipe {sample_id}",
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
    charged = client.post(
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
    return int(charged["id"])


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
        assert body["result_history"] == []
        assert body["crucible"] is None
        assert body["certificates"] == []

    def test_an_unknown_sample_id_is_404(self, client: TestClient) -> None:
        response = client.get("/api/samples/999999")
        assert response.status_code == 404

    def test_after_a_result_the_sample_shows_its_current_grade(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        _charge_into_a_crucible(client, received_sample_id)
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
        _charge_into_a_crucible(client, received_sample_id)
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
        assert len(body["result_history"]) == 2
        assert body["result_history"][0]["id"] == body["current_result"]["id"]
        assert body["result_history"][0]["superseded_reason"] == "re-weighed"
        assert body["result_history"][1]["id"] == first["id"]
        assert body["result_history"][1]["superseded_reason"] is None

    def test_result_history_shows_full_chain_for_three_deep_supersession(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        _charge_into_a_crucible(client, received_sample_id)
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
        second = client.post(
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
        ).json()
        third = client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "gold_bead_mg": "0.250",
                "sample_weight_g": "30",
                "analysed_at": "2026-08-24T16:00:00Z",
                "supersedes_id": second["id"],
                "superseded_reason": "duplicate rejected",
            },
            headers=ANALYST,
        ).json()

        body = client.get(f"/api/samples/{received_sample_id}").json()
        assert body["current_result"]["id"] == third["id"]
        assert len(body["result_history"]) == 3
        assert body["result_history"][0]["id"] == third["id"]
        assert body["result_history"][1]["id"] == second["id"]
        assert body["result_history"][2]["id"] == first["id"]
        assert body["result_history"][2]["superseded_reason"] is None

    def test_sample_detail_includes_crucible_reference_when_wired(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        _charge_into_a_crucible(client, received_sample_id)
        batches = client.get("/api/batches", headers=ANALYST).json()
        batch_id = batches[-1]["id"]
        batch_detail = client.get(f"/api/batches/{batch_id}", headers=ANALYST).json()
        crucible_id = batch_detail["crucibles"][0]["id"]
        for status in ["in_fusion", "fused", "in_cupellation", "cupelled"]:
            client.patch(
                f"/api/batches/{batch_id}/status",
                json={"status": status},
                headers=ANALYST,
            )
        client.post(
            "/api/fire-assay-results",
            json={
                "sample_id": received_sample_id,
                "crucible_id": crucible_id,
                "gold_bead_mg": "0.150",
                "analysed_at": "2026-08-24T14:00:00Z",
            },
            headers=ANALYST,
        )
        response = client.get(f"/api/samples/{received_sample_id}")
        body = response.json()
        assert body["crucible"] is not None
        assert body["crucible"]["id"] == crucible_id
        assert body["crucible"]["batch_id"] == batch_id
        assert body["crucible"]["position_row"] == 1
        assert body["crucible"]["position_col"] == 1
        assert body["crucible"]["status"] is not None

    def test_after_a_certificate_the_sample_lists_it_and_is_reported(
        self, client: TestClient, registered_client_id: int, received_sample_id: int
    ) -> None:
        _charge_into_a_crucible(client, received_sample_id)
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
        _charge_into_a_crucible(client, received_sample_id)
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
        body = response.json()
        assert body["items"] == []
        assert body["next_cursor"] is None

    def test_a_received_sample_appears_with_its_client_and_submission(
        self, client: TestClient, received_sample_id: int
    ) -> None:
        response = client.get("/api/samples")
        assert response.status_code == 200
        body = response.json()
        items = body["items"]
        assert len(items) == 1
        row = items[0]
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
        row = client.get("/api/samples").json()["items"][0]
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
        assert [row["sample_id"] for row in body["items"]] == [
            "MSA-24-SO-00002",
            "MSA-24-SO-00001",
        ]

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
        _charge_into_a_crucible(client, received_sample_id)
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
        assert [row["id"] for row in assayed["items"]] == [received_sample_id]

        received = client.get("/api/samples", params={"status": "received"}).json()
        assert [row["sample_id"] for row in received["items"]] == ["MSA-24-SO-00099"]

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
        assert [row["sample_id"] for row in body["items"]] == ["MSA-24-SO-00001"]

    def test_an_invalid_status_value_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/samples", params={"status": "not-a-real-status"})
        assert response.status_code == 422


class TestCursorPagination:
    def test_cursor_returns_next_page(self, client: TestClient, registered_client_id: int) -> None:
        """Creating several samples and paginating through them with limit=1."""
        ids = []
        for i in range(3):
            sub = client.post(
                "/api/submissions",
                json={
                    "client_id": registered_client_id,
                    "received_at": "2026-08-24T10:00:00Z",
                    "samples": [{"sample_id": f"MSA-24-SO-{i:05d}", "sample_type": "soil"}],
                },
                headers=ANALYST,
            ).json()
            ids.append(sub["samples"][0]["id"])

        # Page 1 — newest (id 3)
        page1 = client.get("/api/samples", params={"limit": 1}).json()
        assert len(page1["items"]) == 1
        assert page1["items"][0]["id"] == ids[2]
        cursor = page1["next_cursor"]
        assert cursor is not None

        # Page 2 — middle (id 2)
        page2 = client.get("/api/samples", params={"limit": 1, "cursor": cursor}).json()
        assert len(page2["items"]) == 1
        assert page2["items"][0]["id"] == ids[1]
        cursor2 = page2["next_cursor"]
        assert cursor2 is not None

        # Page 3 — oldest (id 1)
        page3 = client.get("/api/samples", params={"limit": 1, "cursor": cursor2}).json()
        assert len(page3["items"]) == 1
        assert page3["items"][0]["id"] == ids[0]
        assert page3["next_cursor"] is None

    def test_cursor_filters_out_returned_ids(
        self, client: TestClient, registered_client_id: int
    ) -> None:
        """The cursor is exclusive — items already seen don't reappear."""
        sub = client.post(
            "/api/submissions",
            json={
                "client_id": registered_client_id,
                "received_at": "2026-08-24T10:00:00Z",
                "samples": [
                    {"sample_id": "MSA-24-SO-00100", "sample_type": "soil"},
                    {"sample_id": "MSA-24-SO-00101", "sample_type": "soil"},
                ],
            },
            headers=ANALYST,
        ).json()
        ids = [s["id"] for s in sub["samples"]]

        page1 = client.get("/api/samples", params={"limit": 1}).json()
        first_id = page1["items"][0]["id"]
        assert first_id == ids[1]  # newest first

        page2 = client.get(
            "/api/samples", params={"limit": 1, "cursor": page1["next_cursor"]}
        ).json()
        assert page2["items"][0]["id"] == ids[0]
        assert first_id not in [r["id"] for r in page2["items"]]

    def test_empty_page_when_cursor_exhausted(self, client: TestClient) -> None:
        """A cursor past the end returns an empty page with next_cursor=None."""
        body = client.get("/api/samples", params={"cursor": 999999}).json()
        assert body["items"] == []
        assert body["next_cursor"] is None
