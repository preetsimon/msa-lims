"""Client and project registration: the accounts the lab does work for.

Thinner than submission intake — no label parsing, no depth-interval checking
— but the same two disciplines apply. **Check every constraint before writing
anything**, so a re-submitted form comes back with a clear reason rather than
an unhandled database error: the unique client code, the unique client name,
and a project name unique within its client are all checked here rather than
left to surface as a raw ``IntegrityError``. And **every row created is
audited**, matching the grain used everywhere else in this schema — one event
per record, not one summarising the whole request.

:class:`ClientNotFoundError` lives here rather than in
:mod:`msa_lims.submissions.service`, even though submission intake is what
first needed it — both features ask the same question ("does this client
exist?") and a caller catching one must not be able to miss the other because
two unrelated classes happened to share a name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, Client, LabUser, Project
from msa_lims.domain.enums import MAY_MANAGE_ACCOUNTS, Role
from msa_lims.domain.lifecycle import InsufficientRoleError


class ClientNotFoundError(ValueError):
    """No client with this id exists."""


class ProjectNotFoundError(ValueError):
    """No project with this id exists.

    Lives here rather than in :mod:`msa_lims.drill_holes.service`, for the same
    reason :class:`ClientNotFoundError` lives here rather than in
    :mod:`msa_lims.submissions.service`: the module that owns an entity owns
    the error for "this one doesn't exist," so every caller asking the same
    question gets the same answer type.
    """


class ClientValidationError(ValueError):
    """One or more problems with a client registration, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


class ProjectValidationError(ValueError):
    """One or more problems with a project registration, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class ClientInput:
    code: str
    name: str
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    billing_address: str | None = None


@dataclass(frozen=True, slots=True)
class ProjectInput:
    client_id: int
    name: str
    description: str | None = None
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None


def _audit(
    session: Session, table_name: str, record_id: int, actor: LabUser, after: dict[str, object]
) -> None:
    session.add(
        AuditEvent(
            table_name=table_name,
            record_id=record_id,
            action="create",
            after=after,
            actor_id=actor.id,
        )
    )


class ClientService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: ClientInput, *, registered_by: LabUser, actor_role: Role) -> Client:
        if actor_role not in MAY_MANAGE_ACCOUNTS:
            raise InsufficientRoleError(
                f"{actor_role.value} may not register a client; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_MANAGE_ACCOUNTS))
            )

        problems: list[str] = []

        code = data.code.strip().upper()
        if not code:
            problems.append("client code cannot be blank")
        elif self._session.scalar(select(Client).where(Client.code == code)) is not None:
            problems.append(f"client code {code!r} is already in use")

        name = data.name.strip()
        if not name:
            problems.append("client name cannot be blank")
        elif self._session.scalar(select(Client).where(Client.name == name)) is not None:
            problems.append(f"a client named {name!r} already exists")

        if problems:
            raise ClientValidationError(problems)

        client = Client(
            code=code,
            name=name,
            contact_person=data.contact_person,
            email=data.email,
            phone=data.phone,
            billing_address=data.billing_address,
        )
        self._session.add(client)
        self._session.flush()

        _audit(
            self._session,
            "client",
            client.id,
            registered_by,
            after={"code": client.code, "name": client.name},
        )
        return client


class ProjectService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: ProjectInput, *, registered_by: LabUser, actor_role: Role) -> Project:
        if actor_role not in MAY_MANAGE_ACCOUNTS:
            raise InsufficientRoleError(
                f"{actor_role.value} may not register a project; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_MANAGE_ACCOUNTS))
            )

        client = self._session.get(Client, data.client_id)
        if client is None:
            raise ClientNotFoundError(f"no client with id {data.client_id}")

        problems: list[str] = []

        name = data.name.strip()
        if not name:
            problems.append("project name cannot be blank")
        elif (
            self._session.scalar(
                select(Project).where(Project.client_id == client.id, Project.name == name)
            )
            is not None
        ):
            problems.append(f"{client.name!r} already has a project named {name!r}")

        if (
            data.start_date is not None
            and data.end_date is not None
            and data.end_date < data.start_date
        ):
            problems.append("end date cannot be before start date")

        if problems:
            raise ProjectValidationError(problems)

        project = Project(
            client_id=client.id,
            name=name,
            description=data.description,
            location=data.location,
            start_date=data.start_date,
            end_date=data.end_date,
        )
        self._session.add(project)
        self._session.flush()

        _audit(
            self._session,
            "project",
            project.id,
            registered_by,
            after={"client_id": client.id, "name": project.name},
        )
        return project
