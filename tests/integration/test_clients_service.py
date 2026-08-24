"""Client and project registration, against a real Postgres session."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.clients.service import (
    ClientInput,
    ClientNotFoundError,
    ClientService,
    ClientValidationError,
    ProjectInput,
    ProjectService,
    ProjectValidationError,
)
from msa_lims.db.models import AuditEvent, LabUser
from msa_lims.domain.enums import Role
from msa_lims.domain.lifecycle import InsufficientRoleError

pytestmark = pytest.mark.integration


@pytest.fixture
def manager(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-manager-1", email="m@lab.test", full_name="M. Anager", role=Role.LAB_MANAGER
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-2", email="a2@lab.test", full_name="A. Nalyst", role=Role.ANALYST
    )
    app_session.add(user)
    app_session.flush()
    return user


class TestRegisteringAClient:
    def test_a_client_is_registered(self, app_session: Session, manager: LabUser) -> None:
        service = ClientService(app_session)
        client = service.create(
            ClientInput(code="msa", name="MSA Test Mining Co", email="ops@msa.test"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        assert client.code == "MSA"  # normalised to uppercase
        assert client.name == "MSA Test Mining Co"
        assert client.is_active is True

    def test_a_supervisor_may_also_register_a_client(
        self, app_session: Session, manager: LabUser
    ) -> None:
        service = ClientService(app_session)
        client = service.create(
            ClientInput(code="SUP", name="Supervisor-Registered Co"),
            registered_by=manager,
            actor_role=Role.SUPERVISOR,
        )
        assert client.id is not None

    def test_an_analyst_may_not_register_a_client(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        service = ClientService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create(
                ClientInput(code="MSA", name="MSA Test Mining Co"),
                registered_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_duplicate_code_is_refused(self, app_session: Session, manager: LabUser) -> None:
        service = ClientService(app_session)
        service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        with pytest.raises(ClientValidationError, match="already in use"):
            service.create(
                ClientInput(code="msa", name="A Different Company"),
                registered_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_a_duplicate_name_is_refused(self, app_session: Session, manager: LabUser) -> None:
        service = ClientService(app_session)
        service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        with pytest.raises(ClientValidationError, match="already exists"):
            service.create(
                ClientInput(code="MSA2", name="MSA Test Mining Co"),
                registered_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_a_blank_code_is_refused(self, app_session: Session, manager: LabUser) -> None:
        service = ClientService(app_session)
        with pytest.raises(ClientValidationError, match="cannot be blank"):
            service.create(
                ClientInput(code="   ", name="MSA Test Mining Co"),
                registered_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_registering_a_client_writes_an_audit_event(
        self, app_session: Session, manager: LabUser
    ) -> None:
        service = ClientService(app_session)
        client = service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        events = app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "client", AuditEvent.record_id == client.id
            )
        ).all()
        assert len(events) == 1
        assert events[0].actor_id == manager.id


class TestRegisteringAProject:
    def test_a_project_is_registered(self, app_session: Session, manager: LabUser) -> None:
        client_service = ClientService(app_session)
        client = client_service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        project_service = ProjectService(app_session)
        project = project_service.create(
            ProjectInput(client_id=client.id, name="2024 Drill Program", location="Red Lake, ON"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        assert project.client_id == client.id
        assert project.name == "2024 Drill Program"

    def test_an_unknown_client_is_refused(self, app_session: Session, manager: LabUser) -> None:
        service = ProjectService(app_session)
        with pytest.raises(ClientNotFoundError):
            service.create(
                ProjectInput(client_id=999_999, name="Ghost Program"),
                registered_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_an_analyst_may_not_register_a_project(
        self, app_session: Session, manager: LabUser, analyst: LabUser
    ) -> None:
        client_service = ClientService(app_session)
        client = client_service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        service = ProjectService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create(
                ProjectInput(client_id=client.id, name="2024 Drill Program"),
                registered_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_duplicate_project_name_within_the_same_client_is_refused(
        self, app_session: Session, manager: LabUser
    ) -> None:
        client_service = ClientService(app_session)
        client = client_service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        service = ProjectService(app_session)
        service.create(
            ProjectInput(client_id=client.id, name="2024 Drill Program"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        with pytest.raises(ProjectValidationError, match="already has a project"):
            service.create(
                ProjectInput(client_id=client.id, name="2024 Drill Program"),
                registered_by=manager,
                actor_role=Role.LAB_MANAGER,
            )

    def test_the_same_project_name_is_fine_for_a_different_client(
        self, app_session: Session, manager: LabUser
    ) -> None:
        client_service = ClientService(app_session)
        client_a = client_service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        client_b = client_service.create(
            ClientInput(code="OTH", name="Other Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        service = ProjectService(app_session)
        service.create(
            ProjectInput(client_id=client_a.id, name="2024 Drill Program"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        second = service.create(
            ProjectInput(client_id=client_b.id, name="2024 Drill Program"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        assert second.id is not None

    def test_an_end_date_before_the_start_date_is_refused(
        self, app_session: Session, manager: LabUser
    ) -> None:
        client_service = ClientService(app_session)
        client = client_service.create(
            ClientInput(code="MSA", name="MSA Test Mining Co"),
            registered_by=manager,
            actor_role=Role.LAB_MANAGER,
        )
        app_session.flush()

        service = ProjectService(app_session)
        with pytest.raises(ProjectValidationError, match="end date"):
            service.create(
                ProjectInput(
                    client_id=client.id,
                    name="Backwards Program",
                    start_date=date(2026, 6, 1),
                    end_date=date(2026, 1, 1),
                ),
                registered_by=manager,
                actor_role=Role.LAB_MANAGER,
            )
