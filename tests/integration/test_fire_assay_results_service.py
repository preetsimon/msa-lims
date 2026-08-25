"""Fire assay result entry, against a real Postgres session.

The two properties that matter most: results are genuinely append-only (never
UPDATE, never DELETE — proven against the restricted role directly, matching
``test_append_only.py``'s pattern), and a supersession chain cannot branch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, Client, LabUser, Sample, Submission
from msa_lims.domain.enums import Role, SampleStatus, SampleType
from msa_lims.domain.lifecycle import InsufficientRoleError
from msa_lims.fire_assay_results.service import (
    FireAssayResultInput,
    FireAssayResultService,
    FireAssayResultValidationError,
    SampleNotFoundError,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-far-1",
        email="far1@lab.test",
        full_name="A. Nalyst",
        role=Role.ANALYST,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def supervisor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-supervisor-far-1",
        email="sup1@lab.test",
        full_name="S. Upervisor",
        role=Role.SUPERVISOR,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def a_sample(app_session: Session) -> Sample:
    client = Client(code="MSA", name="MSA Test Mining Co")
    app_session.add(client)
    app_session.flush()

    submission = Submission(
        submission_number="SUB-2026-9001",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    app_session.add(submission)
    app_session.flush()

    sample = Sample(
        sample_id="MSA-24-SO-00417",
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        status=SampleStatus.RECEIVED,
    )
    app_session.add(sample)
    app_session.flush()
    return sample


def result_input(**overrides: object) -> FireAssayResultInput:
    defaults: dict[str, object] = {
        "sample_id": 1,
        "gold_bead_mg": Decimal("0.150"),
        "sample_weight_g": Decimal("30"),
        "balance_sensitivity_mg": None,
        "analysed_at": datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
        "notes": None,
        "supersedes_id": None,
        "superseded_reason": None,
    }
    defaults.update(overrides)
    return FireAssayResultInput(**defaults)  # type: ignore[arg-type]


class TestEnteringAResult:
    def test_the_grade_is_computed_from_the_weighing(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        # 0.150 mg of gold from 30 g of rock is 5 g/t.
        assert result.au_value == Decimal("5")
        assert result.au_censored is False
        assert result.au_unit == "g/t"
        assert result.method.value == "fire_assay_gravimetric"

    def test_entering_a_result_moves_the_sample_to_assayed(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()
        assert a_sample.status is SampleStatus.ASSAYED

    def test_a_bead_below_sensitivity_is_stored_as_a_non_detect(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(
                sample_id=a_sample.id,
                gold_bead_mg=Decimal("0.0005"),
                balance_sensitivity_mg=Decimal("0.001"),
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        assert result.au_censored is True
        assert result.au_value is None
        assert result.au_detection_limit is not None

    def test_a_prep_tech_may_not_enter_a_result(
        self, app_session: Session, a_sample: Sample
    ) -> None:
        prep_tech = LabUser(
            subject="sub-prep-far-1", email="p1@lab.test", full_name="P. Rep", role=Role.PREP_TECH
        )
        app_session.add(prep_tech)
        app_session.flush()

        service = FireAssayResultService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create(
                result_input(sample_id=a_sample.id), analyst=prep_tech, actor_role=Role.PREP_TECH
            )

    def test_an_unknown_sample_is_refused(self, app_session: Session, analyst: LabUser) -> None:
        service = FireAssayResultService(app_session)
        with pytest.raises(SampleNotFoundError):
            service.create(
                result_input(sample_id=999_999), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_rejected_sample_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        a_sample.status = SampleStatus.REJECTED
        app_session.flush()

        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="rejected"):
            service.create(
                result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_second_new_result_against_an_already_resulted_sample_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="already has a result"):
            service.create(
                result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_negative_weight_from_domain_assay_surfaces_as_the_domain_error(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        from msa_lims.domain.assay import AssayCalculationError

        service = FireAssayResultService(app_session)
        with pytest.raises(AssayCalculationError):
            service.create(
                result_input(sample_id=a_sample.id, sample_weight_g=Decimal("0")),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_entering_a_result_writes_an_audit_event(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        events = app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "fire_assay_result", AuditEvent.record_id == result.id
            )
        ).all()
        assert len(events) == 1
        assert events[0].action == "create"
        assert events[0].actor_id == analyst.id


class TestSupersession:
    def test_a_supervisor_corrects_a_result(
        self, app_session: Session, analyst: LabUser, supervisor: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        second = service.create(
            result_input(
                sample_id=a_sample.id,
                gold_bead_mg=Decimal("0.160"),
                supersedes_id=first.id,
                superseded_reason="transcription error at the balance",
            ),
            analyst=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()

        assert second.supersedes_id == first.id
        assert second.au_value != first.au_value

    def test_superseding_writes_an_amend_audit_event_with_the_reason(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        second = service.create(
            result_input(
                sample_id=a_sample.id,
                supersedes_id=first.id,
                superseded_reason="re-weighed after balance drift found",
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "fire_assay_result", AuditEvent.record_id == second.id
            )
        )
        assert event is not None
        assert event.action == "amend"
        assert event.reason == "re-weighed after balance drift found"

    def test_superseding_without_a_reason_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="requires a reason"):
            service.create(
                result_input(sample_id=a_sample.id, supersedes_id=first.id),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_superseding_a_row_that_is_not_current_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """Prevents a branching chain: only the head can be corrected."""
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()
        service.create(
            result_input(
                sample_id=a_sample.id,
                supersedes_id=first.id,
                superseded_reason="first correction",
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="only the current result"):
            service.create(
                result_input(
                    sample_id=a_sample.id,
                    supersedes_id=first.id,  # already superseded once
                    superseded_reason="second attempt at correcting the original",
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_superseding_a_result_from_another_sample_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        other_submission_client = Client(code="OTH", name="Other Mining Co")
        app_session.add(other_submission_client)
        app_session.flush()
        other_submission = Submission(
            submission_number="SUB-2026-9002",
            client_id=other_submission_client.id,
            received_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
        app_session.add(other_submission)
        app_session.flush()
        other_sample = Sample(
            sample_id="MSA-24-SO-00418",
            submission_id=other_submission.id,
            sample_type=SampleType.SOIL,
            status=SampleStatus.RECEIVED,
        )
        app_session.add(other_sample)
        app_session.flush()

        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="not the current result"):
            service.create(
                result_input(
                    sample_id=other_sample.id,
                    supersedes_id=first.id,
                    superseded_reason="wrong sample entirely",
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_superseding_does_not_touch_sample_status(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()
        assert a_sample.status is SampleStatus.ASSAYED

        service.create(
            result_input(
                sample_id=a_sample.id, supersedes_id=first.id, superseded_reason="a correction"
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        assert a_sample.status is SampleStatus.ASSAYED


class TestAppendOnly:
    def test_the_application_role_cannot_update_a_result(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        from sqlalchemy import text

        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("UPDATE fire_assay_result SET au_value = 999 WHERE id = :id"),
                {"id": result.id},
            )

    def test_the_application_role_cannot_delete_a_result(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        from sqlalchemy import text

        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("DELETE FROM fire_assay_result WHERE id = :id"), {"id": result.id}
            )
