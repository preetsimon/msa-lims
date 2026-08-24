"""Submission intake, against a real Postgres session under the restricted role.

Every write the service makes — `client`/`project`/`drill_hole`/`submission`/
`sample` (mutable), `audit_event` (append-only) — goes through `msa_app`, the
exact role the deployed application holds. A service that needed a forbidden
grant fails here rather than in deployment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, Client, DrillHole, LabUser, Project, Sample
from msa_lims.domain.enums import Role, SampleStatus, SampleType
from msa_lims.domain.lifecycle import InsufficientRoleError
from msa_lims.submissions.service import (
    ClientNotFoundError,
    SampleInput,
    SubmissionInput,
    SubmissionService,
    SubmissionValidationError,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-1", email="a@lab.test", full_name="A. Nalyst", role=Role.ANALYST
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


@pytest.fixture
def a_project(app_session: Session, a_client: Client) -> Project:
    project = Project(client_id=a_client.id, name="2024 Drill Program")
    app_session.add(project)
    app_session.flush()
    return project


@pytest.fixture
def a_hole(app_session: Session, a_project: Project) -> DrillHole:
    hole = DrillHole(project_id=a_project.id, hole_id="MSA-24-001")
    app_session.add(hole)
    app_session.flush()
    return hole


@pytest.fixture
def service(app_session: Session) -> SubmissionService:
    return SubmissionService(app_session)


def submission_input(**overrides: object) -> SubmissionInput:
    defaults: dict[str, object] = {
        "client_id": 1,
        "project_id": None,
        "client_reference": None,
        "purchase_order": None,
        "received_at": datetime(2026, 8, 24, tzinfo=UTC),
        "declared_sample_count": None,
        "rush": False,
        "requested_tat_days": None,
        "comments": None,
        "samples": (),
    }
    defaults.update(overrides)
    return SubmissionInput(**defaults)  # type: ignore[arg-type]


class TestReceivingASubmission:
    def test_a_surface_sample_is_received(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
    ) -> None:
        submission = service.create(
            submission_input(
                client_id=a_client.id,
                samples=(SampleInput("MSA-24-SO-00417", SampleType.SOIL),),
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert submission.submission_number.startswith("SUB-2026-")
        assert len(submission.samples) == 1
        sample = submission.samples[0]
        assert sample.sample_id == "MSA-24-SO-00417"
        assert sample.status is SampleStatus.RECEIVED
        assert sample.drill_hole_id is None

    def test_a_drill_sample_resolves_its_hole_and_interval(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
        a_project: Project,
        a_hole: DrillHole,
    ) -> None:
        submission = service.create(
            submission_input(
                client_id=a_client.id,
                project_id=a_project.id,
                samples=(SampleInput("MSA-24-001-142.50_144.00", SampleType.CORE),),
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        sample = submission.samples[0]
        assert sample.drill_hole_id == a_hole.id
        assert sample.from_depth_m == Decimal("142.50")
        assert sample.to_depth_m == Decimal("144.00")

    def test_a_second_submission_gets_the_next_number_in_sequence(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
    ) -> None:
        first = service.create(
            submission_input(
                client_id=a_client.id, samples=(SampleInput("MSA-24-SO-00001", SampleType.SOIL),)
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()
        second = service.create(
            submission_input(
                client_id=a_client.id, samples=(SampleInput("MSA-24-SO-00002", SampleType.SOIL),)
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()
        assert first.submission_number != second.submission_number

    def test_client_role_may_not_receive_a_submission(
        self,
        service: SubmissionService,
        analyst: LabUser,
        a_client: Client,
    ) -> None:
        with pytest.raises(InsufficientRoleError):
            service.create(
                submission_input(
                    client_id=a_client.id,
                    samples=(SampleInput("MSA-24-SO-00417", SampleType.SOIL),),
                ),
                received_by=analyst,
                actor_role=Role.CLIENT,
            )

    def test_an_unknown_client_is_refused(
        self, service: SubmissionService, analyst: LabUser
    ) -> None:
        with pytest.raises(ClientNotFoundError):
            service.create(
                submission_input(
                    client_id=999_999,
                    samples=(SampleInput("MSA-24-SO-00417", SampleType.SOIL),),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )


class TestValidation:
    def test_every_problem_is_reported_in_one_pass(
        self, service: SubmissionService, analyst: LabUser, a_client: Client
    ) -> None:
        with pytest.raises(SubmissionValidationError) as caught:
            service.create(
                submission_input(
                    client_id=a_client.id,
                    samples=(
                        SampleInput("not a label", SampleType.SOIL),
                        # a drill sample with no project named on the submission
                        SampleInput("MSA-24-001-1_2", SampleType.CORE),
                    ),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )
        assert len(caught.value.problems) == 2

    def test_duplicate_labels_within_one_submission_are_refused(
        self, service: SubmissionService, analyst: LabUser, a_client: Client
    ) -> None:
        with pytest.raises(SubmissionValidationError, match="more than once"):
            service.create(
                submission_input(
                    client_id=a_client.id,
                    samples=(
                        SampleInput("MSA-24-SO-00417", SampleType.SOIL),
                        SampleInput("msa-24-so-00417", SampleType.SOIL),
                    ),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_label_already_received_in_an_earlier_submission_is_refused(
        self, service: SubmissionService, app_session: Session, analyst: LabUser, a_client: Client
    ) -> None:
        service.create(
            submission_input(
                client_id=a_client.id, samples=(SampleInput("MSA-24-SO-00417", SampleType.SOIL),)
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(SubmissionValidationError, match="already been received"):
            service.create(
                submission_input(
                    client_id=a_client.id,
                    samples=(SampleInput("MSA-24-SO-00417", SampleType.SOIL),),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_drill_sample_without_a_project_is_refused(
        self, service: SubmissionService, analyst: LabUser, a_client: Client
    ) -> None:
        with pytest.raises(SubmissionValidationError, match="no project"):
            service.create(
                submission_input(
                    client_id=a_client.id, samples=(SampleInput("MSA-24-001-1_2", SampleType.CORE),)
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_drill_sample_referencing_an_unregistered_hole_is_refused(
        self,
        service: SubmissionService,
        analyst: LabUser,
        a_client: Client,
        a_project: Project,
    ) -> None:
        with pytest.raises(SubmissionValidationError, match="register the hole"):
            service.create(
                submission_input(
                    client_id=a_client.id,
                    project_id=a_project.id,
                    samples=(SampleInput("MSA-24-099-1_2", SampleType.CORE),),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_project_from_another_client_is_refused(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
        a_project: Project,
    ) -> None:
        other_client = Client(code="OTH", name="Other Mining Co")
        app_session.add(other_client)
        app_session.flush()

        with pytest.raises(SubmissionValidationError, match="different client"):
            service.create(
                submission_input(
                    client_id=other_client.id,
                    project_id=a_project.id,  # belongs to a_client, not other_client
                    samples=(SampleInput("MSA-24-SO-00417", SampleType.SOIL),),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_overlapping_intervals_in_the_same_hole_are_refused(
        self,
        service: SubmissionService,
        analyst: LabUser,
        a_client: Client,
        a_project: Project,
        a_hole: DrillHole,
    ) -> None:
        with pytest.raises(SubmissionValidationError, match="overlap"):
            service.create(
                submission_input(
                    client_id=a_client.id,
                    project_id=a_project.id,
                    samples=(
                        SampleInput("MSA-24-001-140_145", SampleType.CORE),
                        SampleInput("MSA-24-001-142_143", SampleType.CORE),
                    ),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_contiguous_intervals_do_not_conflict(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
        a_project: Project,
        a_hole: DrillHole,
    ) -> None:
        submission = service.create(
            submission_input(
                client_id=a_client.id,
                project_id=a_project.id,
                samples=(
                    SampleInput("MSA-24-001-140_142", SampleType.CORE),
                    SampleInput("MSA-24-001-142_144", SampleType.CORE),
                ),
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()
        assert len(submission.samples) == 2

    def test_a_new_sample_overlapping_an_already_received_one_is_refused(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
        a_project: Project,
        a_hole: DrillHole,
    ) -> None:
        service.create(
            submission_input(
                client_id=a_client.id,
                project_id=a_project.id,
                samples=(SampleInput("MSA-24-001-140_145", SampleType.CORE),),
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(SubmissionValidationError, match="overlap"):
            service.create(
                submission_input(
                    client_id=a_client.id,
                    project_id=a_project.id,
                    samples=(SampleInput("MSA-24-001-141_142", SampleType.CORE),),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_nothing_is_written_when_validation_fails(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
    ) -> None:
        """Validate-everything-before-writing-anything, checked directly: a
        batch with one bad label among good ones leaves no partial submission
        or sample rows behind."""
        with pytest.raises(SubmissionValidationError):
            service.create(
                submission_input(
                    client_id=a_client.id,
                    samples=(
                        SampleInput("MSA-24-SO-00001", SampleType.SOIL),
                        SampleInput("not a label", SampleType.SOIL),
                    ),
                ),
                received_by=analyst,
                actor_role=Role.ANALYST,
            )
        assert (
            app_session.scalar(select(Sample).where(Sample.sample_id == "MSA-24-SO-00001")) is None
        )


class TestAuditTrail:
    def test_creating_a_submission_writes_an_audit_event_per_row(
        self,
        service: SubmissionService,
        app_session: Session,
        analyst: LabUser,
        a_client: Client,
    ) -> None:
        submission = service.create(
            submission_input(
                client_id=a_client.id,
                samples=(
                    SampleInput("MSA-24-SO-00417", SampleType.SOIL),
                    SampleInput("MSA-24-SO-00418", SampleType.SOIL),
                ),
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        submission_events = app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "submission", AuditEvent.record_id == submission.id
            )
        ).all()
        assert len(submission_events) == 1
        assert submission_events[0].actor_id == analyst.id
        assert submission_events[0].action == "create"

        sample_ids = [s.id for s in submission.samples]
        sample_events = app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "sample", AuditEvent.record_id.in_(sample_ids)
            )
        ).all()
        assert len(sample_events) == 2
