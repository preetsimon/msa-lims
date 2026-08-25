"""Drill hole registration, against a real Postgres session.

The point that matters most here isn't covered by asserting the row was
created — it's proven in ``TestCanonicalisationMatchesSubmissionIntake``,
which checks that a hole registered through this service is the exact row
submission intake's own lookup finds for a drill sample's parsed label.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.clients.service import ProjectNotFoundError
from msa_lims.db.models import AuditEvent, Client, LabUser, Project
from msa_lims.domain.enums import Role, SampleType
from msa_lims.domain.lifecycle import InsufficientRoleError
from msa_lims.drill_holes.service import DrillHoleInput, DrillHoleService, DrillHoleValidationError
from msa_lims.submissions.service import SampleInput, SubmissionInput, SubmissionService

pytestmark = pytest.mark.integration


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-3", email="a3@lab.test", full_name="A. Nalyst", role=Role.ANALYST
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


class TestRegisteringADrillHole:
    def test_a_hole_is_registered(
        self, app_session: Session, analyst: LabUser, a_project: Project
    ) -> None:
        service = DrillHoleService(app_session)
        hole = service.create(
            DrillHoleInput(
                project_id=a_project.id,
                hole_id="msa-24-001",
                easting=Decimal("450000"),
                northing=Decimal("5510000"),
                total_depth_m=Decimal("250"),
                dip_degrees=Decimal("-60"),
                azimuth_degrees=Decimal("045"),
            ),
            registered_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert hole.hole_id == "MSA-24-001"  # normalised to canonical case
        assert hole.project_id == a_project.id
        assert hole.total_depth_m == Decimal("250")

    def test_a_prep_tech_may_also_register_a_hole(
        self, app_session: Session, a_project: Project
    ) -> None:
        prep_tech = LabUser(
            subject="sub-prep-1", email="p@lab.test", full_name="P. Rep", role=Role.PREP_TECH
        )
        app_session.add(prep_tech)
        app_session.flush()

        service = DrillHoleService(app_session)
        hole = service.create(
            DrillHoleInput(project_id=a_project.id, hole_id="MSA-24-002"),
            registered_by=prep_tech,
            actor_role=Role.PREP_TECH,
        )
        assert hole.id is not None

    def test_a_client_role_may_not_register_a_hole(
        self, app_session: Session, analyst: LabUser, a_project: Project
    ) -> None:
        service = DrillHoleService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create(
                DrillHoleInput(project_id=a_project.id, hole_id="MSA-24-001"),
                registered_by=analyst,
                actor_role=Role.CLIENT,
            )

    def test_an_unknown_project_is_refused(self, app_session: Session, analyst: LabUser) -> None:
        service = DrillHoleService(app_session)
        with pytest.raises(ProjectNotFoundError):
            service.create(
                DrillHoleInput(project_id=999_999, hole_id="MSA-24-001"),
                registered_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_malformed_hole_id_is_refused(
        self, app_session: Session, analyst: LabUser, a_project: Project
    ) -> None:
        service = DrillHoleService(app_session)
        with pytest.raises(DrillHoleValidationError, match="cannot read"):
            service.create(
                DrillHoleInput(project_id=a_project.id, hole_id="not a hole"),
                registered_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_duplicate_hole_in_the_same_project_is_refused(
        self, app_session: Session, analyst: LabUser, a_project: Project
    ) -> None:
        service = DrillHoleService(app_session)
        service.create(
            DrillHoleInput(project_id=a_project.id, hole_id="MSA-24-001"),
            registered_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(DrillHoleValidationError, match="already has a hole"):
            service.create(
                # Different case, same canonical hole -- must still collide.
                DrillHoleInput(project_id=a_project.id, hole_id="msa-24-001"),
                registered_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_the_same_hole_id_is_fine_under_a_different_project(
        self, app_session: Session, analyst: LabUser, a_client: Client, a_project: Project
    ) -> None:
        other_project = Project(client_id=a_client.id, name="2025 Drill Program")
        app_session.add(other_project)
        app_session.flush()

        service = DrillHoleService(app_session)
        service.create(
            DrillHoleInput(project_id=a_project.id, hole_id="MSA-24-001"),
            registered_by=analyst,
            actor_role=Role.ANALYST,
        )
        second = service.create(
            DrillHoleInput(project_id=other_project.id, hole_id="MSA-24-001"),
            registered_by=analyst,
            actor_role=Role.ANALYST,
        )
        assert second.id is not None

    def test_registering_a_hole_writes_an_audit_event(
        self, app_session: Session, analyst: LabUser, a_project: Project
    ) -> None:
        service = DrillHoleService(app_session)
        hole = service.create(
            DrillHoleInput(project_id=a_project.id, hole_id="MSA-24-001"),
            registered_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        events = app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "drill_hole", AuditEvent.record_id == hole.id
            )
        ).all()
        assert len(events) == 1
        assert events[0].actor_id == analyst.id


class TestCanonicalisationMatchesSubmissionIntake:
    def test_a_hole_registered_in_one_case_is_found_by_a_sample_in_another(
        self, app_session: Session, analyst: LabUser, a_client: Client, a_project: Project
    ) -> None:
        """The property drill_holes/service.py's whole design exists for: a
        hole registered as 'msa-24-001' must be the row a submission's drill
        sample labelled 'MSA-24-001-...' resolves to, with no manual
        case-matching required from whoever is entering data."""
        hole_service = DrillHoleService(app_session)
        registered = hole_service.create(
            DrillHoleInput(project_id=a_project.id, hole_id="msa-24-001"),
            registered_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        submission_service = SubmissionService(app_session)
        submission = submission_service.create(
            SubmissionInput(
                client_id=a_client.id,
                project_id=a_project.id,
                client_reference=None,
                purchase_order=None,
                received_at=datetime(2026, 8, 24, tzinfo=UTC),
                declared_sample_count=None,
                rush=False,
                requested_tat_days=None,
                comments=None,
                samples=(SampleInput("MSA-24-001-142.50_144.00", SampleType.CORE),),
            ),
            received_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert submission.samples[0].drill_hole_id == registered.id
