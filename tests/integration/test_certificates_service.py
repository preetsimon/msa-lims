"""Certificate of Analysis issuance, against a real Postgres session.

The properties that matter most: a sample without a current fire assay
result cannot be certified; a certificate is genuinely append-only (proven
directly against the restricted role, matching ``test_append_only.py``); an
amendment chain cannot branch; and issuing a certificate actually moves an
ASSAYED sample to REPORTED through the real lifecycle transition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from msa_lims.certificates.service import (
    CertificateInput,
    CertificateService,
    CertificateValidationError,
    get_pdf,
)
from msa_lims.clients.service import ClientNotFoundError
from msa_lims.db.models import (
    AuditEvent,
    CertificateResult,
    Client,
    LabUser,
    Sample,
    Submission,
)
from msa_lims.domain.enums import Role, SampleStatus, SampleType
from msa_lims.domain.lifecycle import InsufficientRoleError
from msa_lims.fire_assay_results.service import (
    FireAssayResultInput,
    FireAssayResultService,
    current_result,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def manager(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-manager-coa-1",
        email="mgr-coa1@lab.test",
        full_name="M. Anager",
        role=Role.LAB_MANAGER,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-coa-1",
        email="an-coa1@lab.test",
        full_name="A. Nalyst",
        role=Role.ANALYST,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def a_client(app_session: Session) -> Client:
    client = Client(code="MSA", name="MSA Test Mining Co")
    app_session.add(client)
    app_session.flush()
    return client


_submission_serial = count(1)


def _make_sample(session: Session, client: Client, label: str) -> Sample:
    submission = Submission(
        submission_number=f"SUB-2026-{next(_submission_serial):04d}",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    session.add(submission)
    session.flush()
    sample = Sample(
        sample_id=label,
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        status=SampleStatus.RECEIVED,
    )
    session.add(sample)
    session.flush()
    return sample


@pytest.fixture
def an_assayed_sample(app_session: Session, analyst: LabUser, a_client: Client) -> Sample:
    sample = _make_sample(app_session, a_client, "MSA-24-SO-90001")
    FireAssayResultService(app_session).create(
        FireAssayResultInput(
            sample_id=sample.id,
            gold_bead_mg=Decimal("0.150"),
            sample_weight_g=Decimal("30"),
            balance_sensitivity_mg=None,
            analysed_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
        ),
        analyst=analyst,
        actor_role=Role.ANALYST,
    )
    app_session.flush()
    return sample


def cert_input(**overrides: object) -> CertificateInput:
    defaults: dict[str, object] = {
        "client_id": 1,
        "sample_ids": (),
        "issued_at": datetime(2026, 8, 24, 15, 0, tzinfo=UTC),
        "notes": None,
        "supersedes_id": None,
        "superseded_reason": None,
    }
    defaults.update(overrides)
    return CertificateInput(**defaults)  # type: ignore[arg-type]


class TestIssuingACertificate:
    def test_a_certificate_is_issued(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        certificate = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        assert certificate.certificate_number.startswith("COA-2026-")
        assert certificate.pdf_bytes.startswith(b"%PDF-")
        assert len(certificate.pdf_sha256) == 64

    def test_issuing_moves_the_sample_to_reported(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()
        assert an_assayed_sample.status is SampleStatus.REPORTED

    def test_an_analyst_may_not_issue_a_certificate(
        self,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create(
                cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
                issued_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_an_unknown_client_is_refused(self, app_session: Session, manager: LabUser) -> None:
        service = CertificateService(app_session)
        with pytest.raises(ClientNotFoundError):
            service.create(
                cert_input(client_id=999_999, sample_ids=(1,)),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_a_sample_with_no_current_result_is_refused(
        self, app_session: Session, manager: LabUser, a_client: Client
    ) -> None:
        unassayed = _make_sample(app_session, a_client, "MSA-24-SO-90002")
        service = CertificateService(app_session)
        with pytest.raises(CertificateValidationError, match="no fire assay result"):
            service.create(
                cert_input(client_id=a_client.id, sample_ids=(unassayed.id,)),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_no_samples_at_all_is_refused(
        self, app_session: Session, manager: LabUser, a_client: Client
    ) -> None:
        service = CertificateService(app_session)
        with pytest.raises(CertificateValidationError, match="at least one sample"):
            service.create(
                cert_input(client_id=a_client.id, sample_ids=()),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_a_sample_belonging_to_a_different_client_is_refused(
        self, app_session: Session, manager: LabUser, a_client: Client, an_assayed_sample: Sample
    ) -> None:
        other_client = Client(code="OTH", name="Other Mining Co")
        app_session.add(other_client)
        app_session.flush()

        service = CertificateService(app_session)
        with pytest.raises(CertificateValidationError, match="does not belong to"):
            service.create(
                cert_input(client_id=other_client.id, sample_ids=(an_assayed_sample.id,)),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_duplicate_sample_ids_in_one_request_are_refused(
        self, app_session: Session, manager: LabUser, a_client: Client, an_assayed_sample: Sample
    ) -> None:
        service = CertificateService(app_session)
        with pytest.raises(CertificateValidationError, match="more than once"):
            service.create(
                cert_input(
                    client_id=a_client.id,
                    sample_ids=(an_assayed_sample.id, an_assayed_sample.id),
                ),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_certifying_two_samples_records_two_certificate_results(
        self, app_session: Session, manager: LabUser, analyst: LabUser, a_client: Client
    ) -> None:
        first = _make_sample(app_session, a_client, "MSA-24-SO-90003")
        second = _make_sample(app_session, a_client, "MSA-24-SO-90004")
        result_service = FireAssayResultService(app_session)
        for sample in (first, second):
            result_service.create(
                FireAssayResultInput(
                    sample_id=sample.id,
                    gold_bead_mg=Decimal("0.150"),
                    sample_weight_g=Decimal("30"),
                    balance_sensitivity_mg=None,
                    analysed_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )
        app_session.flush()

        service = CertificateService(app_session)
        certificate = service.create(
            cert_input(client_id=a_client.id, sample_ids=(first.id, second.id)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        rows = app_session.scalars(
            select(CertificateResult).where(CertificateResult.certificate_id == certificate.id)
        ).all()
        assert len(rows) == 2

    def test_issuing_writes_an_audit_event(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        certificate = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "certificate", AuditEvent.record_id == certificate.id
            )
        )
        assert event is not None
        assert event.action == "create"
        assert event.actor_id == manager.id


class TestAmendingACertificate:
    def test_a_manager_amends_a_certificate(
        self,
        app_session: Session,
        manager: LabUser,
        analyst: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        first = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        # Correct the underlying result, then re-issue the certificate against
        # the corrected one.
        original_result = current_result(app_session, an_assayed_sample.id)
        assert original_result is not None
        FireAssayResultService(app_session).create(
            FireAssayResultInput(
                sample_id=an_assayed_sample.id,
                gold_bead_mg=Decimal("0.200"),
                sample_weight_g=Decimal("30"),
                balance_sensitivity_mg=None,
                analysed_at=datetime(2026, 8, 24, 10, 0, tzinfo=UTC),
                supersedes_id=original_result.id,
                superseded_reason="re-weighed after balance drift found",
            ),
            analyst=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        second = service.create(
            cert_input(
                client_id=a_client.id,
                sample_ids=(an_assayed_sample.id,),
                supersedes_id=first.id,
                superseded_reason="underlying result was corrected",
            ),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        assert second.supersedes_id == first.id
        assert second.certificate_number != first.certificate_number

    def test_amending_without_a_reason_is_refused(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        first = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        with pytest.raises(CertificateValidationError, match="requires a reason"):
            service.create(
                cert_input(
                    client_id=a_client.id,
                    sample_ids=(an_assayed_sample.id,),
                    supersedes_id=first.id,
                ),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_amending_an_already_amended_certificate_is_refused(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        """Anti-branching: only the current certificate in a chain can be
        amended again."""
        service = CertificateService(app_session)
        first = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()
        service.create(
            cert_input(
                client_id=a_client.id,
                sample_ids=(an_assayed_sample.id,),
                supersedes_id=first.id,
                superseded_reason="first amendment",
            ),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        with pytest.raises(CertificateValidationError, match="not a current certificate"):
            service.create(
                cert_input(
                    client_id=a_client.id,
                    sample_ids=(an_assayed_sample.id,),
                    supersedes_id=first.id,
                    superseded_reason="second attempt at correcting the original",
                ),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_amending_with_a_certificate_from_another_client_is_refused(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        other_client = Client(code="OTH", name="Other Mining Co")
        app_session.add(other_client)
        app_session.flush()
        other_sample = _make_sample(app_session, other_client, "OTH-24-SO-00001")
        FireAssayResultService(app_session).create(
            FireAssayResultInput(
                sample_id=other_sample.id,
                gold_bead_mg=Decimal("0.150"),
                sample_weight_g=Decimal("30"),
                balance_sensitivity_mg=None,
                analysed_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
            ),
            analyst=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        service = CertificateService(app_session)
        original = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        with pytest.raises(CertificateValidationError, match="different client"):
            service.create(
                cert_input(
                    client_id=other_client.id,
                    sample_ids=(other_sample.id,),
                    supersedes_id=original.id,
                    superseded_reason="wrong client entirely",
                ),
                issued_by=manager,
                actor_role=Role.LAB_MANAGER,
            )


class TestDownload:
    def test_get_pdf_returns_the_verified_bytes(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        certificate = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        row, pdf_bytes = get_pdf(app_session, certificate.id)
        assert pdf_bytes == certificate.pdf_bytes
        assert row.id == certificate.id


class TestAppendOnly:
    def test_the_application_role_cannot_update_a_certificate(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        certificate = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        from sqlalchemy import text

        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("UPDATE certificate SET notes = 'tampered' WHERE id = :id"),
                {"id": certificate.id},
            )

    def test_the_application_role_cannot_delete_a_certificate(
        self,
        app_session: Session,
        manager: LabUser,
        a_client: Client,
        an_assayed_sample: Sample,
    ) -> None:
        service = CertificateService(app_session)
        certificate = service.create(
            cert_input(client_id=a_client.id, sample_ids=(an_assayed_sample.id,)),
            issued_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        from sqlalchemy import text

        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("DELETE FROM certificate WHERE id = :id"), {"id": certificate.id}
            )
