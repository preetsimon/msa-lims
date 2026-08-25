"""Bare sample lifecycle moves, against a real Postgres session.

The state-machine rules themselves (which moves are legal, who may make
them, when a reason is required) are already exhaustively unit-tested in
``tests/unit/test_lifecycle.py`` against ``check_transition`` directly. These
tests are about the service wrapper: does it find the sample, actually call
the real check, apply the move, and write an honest audit event.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, Client, LabUser, Sample, Submission
from msa_lims.domain.enums import Role, SampleStatus, SampleType
from msa_lims.domain.lifecycle import (
    InsufficientRoleError,
    ReasonRequiredError,
    TransitionNotAllowedError,
)
from msa_lims.fire_assay_results.service import SampleNotFoundError
from msa_lims.sample_lifecycle.service import SampleLifecycleService

pytestmark = pytest.mark.integration


@pytest.fixture
def prep_tech(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-prep-lifecycle-1",
        email="prep-lifecycle@lab.test",
        full_name="P. Rep",
        role=Role.PREP_TECH,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def supervisor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-supervisor-lifecycle-1",
        email="sup-lifecycle@lab.test",
        full_name="S. Upervisor",
        role=Role.SUPERVISOR,
    )
    app_session.add(user)
    app_session.flush()
    return user


def make_sample(
    session: Session,
    *,
    sample_id: str = "MSA-24-SO-00417",
    sample_type: SampleType = SampleType.SOIL,
    status: SampleStatus = SampleStatus.RECEIVED,
) -> Sample:
    client = Client(code=f"MSA{sample_id[-2:]}", name=f"MSA Test Mining Co {sample_id}")
    session.add(client)
    session.flush()
    submission = Submission(
        submission_number=f"SUB-2026-{sample_id[-4:]}",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    session.add(submission)
    session.flush()
    sample = Sample(
        sample_id=sample_id,
        submission_id=submission.id,
        sample_type=sample_type,
        status=status,
    )
    session.add(sample)
    session.flush()
    return sample


class TestThePrepWalk:
    def test_a_core_sample_walks_from_received_to_ready_for_assay(
        self, app_session: Session, prep_tech: LabUser
    ) -> None:
        sample = make_sample(app_session, sample_type=SampleType.CORE)
        service = SampleLifecycleService(app_session)

        service.advance(
            sample.id,
            target=SampleStatus.IN_PREP,
            reason=None,
            actor=prep_tech,
            actor_role=Role.PREP_TECH,
        )
        assert sample.status is SampleStatus.IN_PREP

        service.advance(
            sample.id,
            target=SampleStatus.READY_FOR_ASSAY,
            reason=None,
            actor=prep_tech,
            actor_role=Role.PREP_TECH,
        )
        assert sample.status is SampleStatus.READY_FOR_ASSAY

    def test_a_pulp_skips_preparation(self, app_session: Session, prep_tech: LabUser) -> None:
        sample = make_sample(app_session, sample_type=SampleType.PULP)
        service = SampleLifecycleService(app_session)

        service.advance(
            sample.id,
            target=SampleStatus.READY_FOR_ASSAY,
            reason=None,
            actor=prep_tech,
            actor_role=Role.PREP_TECH,
        )
        assert sample.status is SampleStatus.READY_FOR_ASSAY

    def test_a_core_sample_may_not_skip_preparation(
        self, app_session: Session, prep_tech: LabUser
    ) -> None:
        sample = make_sample(app_session, sample_type=SampleType.CORE)
        service = SampleLifecycleService(app_session)

        with pytest.raises(TransitionNotAllowedError, match="only a pulp"):
            service.advance(
                sample.id,
                target=SampleStatus.READY_FOR_ASSAY,
                reason=None,
                actor=prep_tech,
                actor_role=Role.PREP_TECH,
            )

    def test_a_client_role_may_not_start_prep(
        self, app_session: Session, prep_tech: LabUser
    ) -> None:
        sample = make_sample(app_session, sample_type=SampleType.CORE)
        service = SampleLifecycleService(app_session)

        with pytest.raises(InsufficientRoleError):
            service.advance(
                sample.id,
                target=SampleStatus.IN_PREP,
                reason=None,
                actor=prep_tech,
                actor_role=Role.CLIENT,
            )

    def test_an_unknown_sample_is_refused(self, app_session: Session, prep_tech: LabUser) -> None:
        service = SampleLifecycleService(app_session)
        with pytest.raises(SampleNotFoundError):
            service.advance(
                999_999,
                target=SampleStatus.IN_PREP,
                reason=None,
                actor=prep_tech,
                actor_role=Role.PREP_TECH,
            )

    def test_starting_prep_writes_a_transition_audit_event(
        self, app_session: Session, prep_tech: LabUser
    ) -> None:
        sample = make_sample(app_session, sample_type=SampleType.CORE)
        service = SampleLifecycleService(app_session)
        service.advance(
            sample.id,
            target=SampleStatus.IN_PREP,
            reason=None,
            actor=prep_tech,
            actor_role=Role.PREP_TECH,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "sample", AuditEvent.record_id == sample.id
            )
        )
        assert event is not None
        assert event.action == "transition"
        assert event.before == {"status": "received"}
        assert event.after == {"status": "in_prep"}
        assert event.actor_id == prep_tech.id


class TestReAssayAndRejection:
    def test_a_supervisor_returns_an_assayed_sample_for_re_assay(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        sample = make_sample(app_session, status=SampleStatus.ASSAYED)
        service = SampleLifecycleService(app_session)

        service.advance(
            sample.id,
            target=SampleStatus.READY_FOR_ASSAY,
            reason="RPD out of tolerance",
            actor=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        assert sample.status is SampleStatus.READY_FOR_ASSAY

    def test_re_assay_without_a_reason_is_refused(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        sample = make_sample(app_session, status=SampleStatus.ASSAYED)
        service = SampleLifecycleService(app_session)

        with pytest.raises(ReasonRequiredError):
            service.advance(
                sample.id,
                target=SampleStatus.READY_FOR_ASSAY,
                reason=None,
                actor=supervisor,
                actor_role=Role.SUPERVISOR,
            )

    def test_a_supervisor_rejects_received_material(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        sample = make_sample(app_session)
        service = SampleLifecycleService(app_session)

        service.advance(
            sample.id,
            target=SampleStatus.REJECTED,
            reason="bag split in transit, material contaminated",
            actor=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        assert sample.status is SampleStatus.REJECTED

    def test_a_prep_tech_may_not_reject(self, app_session: Session, prep_tech: LabUser) -> None:
        sample = make_sample(app_session)
        service = SampleLifecycleService(app_session)

        with pytest.raises(InsufficientRoleError):
            service.advance(
                sample.id,
                target=SampleStatus.REJECTED,
                reason="bag split",
                actor=prep_tech,
                actor_role=Role.PREP_TECH,
            )

    def test_an_assayed_sample_cannot_be_rejected(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        sample = make_sample(app_session, status=SampleStatus.ASSAYED)
        service = SampleLifecycleService(app_session)

        with pytest.raises(TransitionNotAllowedError, match="amended certificate"):
            service.advance(
                sample.id,
                target=SampleStatus.REJECTED,
                reason="client changed their mind",
                actor=supervisor,
                actor_role=Role.SUPERVISOR,
            )
