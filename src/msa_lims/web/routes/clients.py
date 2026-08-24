"""Client and project registration.

Both endpoints take a flat body with any foreign key as a plain field —
``ProjectCreate.client_id``, matching how ``SubmissionCreate.client_id`` and
``SubmissionCreate.project_id`` already work — rather than nesting projects
under ``/api/clients/{id}/projects``. One convention for "how does a resource
name its parent" is easier to hold in your head than a URL hierarchy that
agrees with the body shape in one place and disagrees in another.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.clients.service import ClientInput, ClientService, ProjectInput, ProjectService
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import ClientCreate, ClientOut, ProjectCreate, ProjectOut

router = APIRouter(prefix="/api", tags=["clients"])


@router.post("/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientCreate, session: SessionDep, actor: ActorDep, registered_by: LabUserDep
) -> ClientOut:
    service = ClientService(session)
    client = service.create(
        ClientInput(
            code=body.code,
            name=body.name,
            contact_person=body.contact_person,
            email=body.email,
            phone=body.phone,
            billing_address=body.billing_address,
        ),
        registered_by=registered_by,
        actor_role=actor.role,
    )
    session.commit()
    return ClientOut.from_model(client)


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate, session: SessionDep, actor: ActorDep, registered_by: LabUserDep
) -> ProjectOut:
    service = ProjectService(session)
    project = service.create(
        ProjectInput(
            client_id=body.client_id,
            name=body.name,
            description=body.description,
            location=body.location,
            start_date=body.start_date,
            end_date=body.end_date,
        ),
        registered_by=registered_by,
        actor_role=actor.role,
    )
    session.commit()
    return ProjectOut.from_model(project)
