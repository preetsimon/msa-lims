"""Client and project registration.

Both endpoints take a flat body with any foreign key as a plain field —
``ProjectCreate.client_id``, matching how ``SubmissionCreate.client_id`` and
``SubmissionCreate.project_id`` already work — rather than nesting projects
under ``/api/clients/{id}/projects``. One convention for "how does a resource
name its parent" is easier to hold in your head than a URL hierarchy that
agrees with the body shape in one place and disagrees in another.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from msa_lims.clients.service import (
    ClientInput,
    ClientNotFoundError,
    ClientService,
    ProjectInput,
    ProjectService,
    get_client,
    list_clients,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import (
    CLIENT_CONFLICT,
    FORBIDDEN_403,
    NOT_FOUND_404,
    merge_responses,
)
from msa_lims.web.schemas import (
    ClientCreate,
    ClientListItemOut,
    ClientOut,
    ClientStatusUpdate,
    ProjectCreate,
    ProjectOut,
)

router = APIRouter(prefix="/api", tags=["clients"])


@router.get("/clients", response_model=list[ClientListItemOut])
def read_clients(
    session: SessionDep,
    actor: InternalActorDep,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ClientListItemOut]:
    """List all clients, newest first, with submission count.

    Lean rows like ``GET /api/samples``: just enough to populate a filter
    dropdown or a client-management table, no per-row deep lookup.
    """
    items = list_clients(session, limit=limit)
    return [
        ClientListItemOut.from_model(item.client, submission_count=item.submission_count)
        for item in items
    ]


@router.get(
    "/clients/{client_id}",
    response_model=ClientOut,
    responses=merge_responses(FORBIDDEN_403, NOT_FOUND_404),
)
def read_client(
    client_id: int,
    session: SessionDep,
    actor: InternalActorDep,
) -> ClientOut:
    try:
        client = get_client(session, client_id)
    except ClientNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return ClientOut.from_model(client)


@router.post(
    "/clients",
    response_model=ClientOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403, CLIENT_CONFLICT),
)
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


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403),
)
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


@router.patch(
    "/clients/{client_id}",
    response_model=ClientOut,
    responses=merge_responses(FORBIDDEN_403, NOT_FOUND_404),
)
def update_client_status(
    client_id: int,
    body: ClientStatusUpdate,
    session: SessionDep,
    actor: ActorDep,
    lab_user: LabUserDep,
) -> ClientOut:
    """Set or clear a client's active flag.

    Deactivating a client prevents new submissions for that client but does
    not touch existing submissions or results.  Reactivation is the same
    call with ``is_active: true``.
    """
    service = ClientService(session)
    try:
        client = service.deactivate(
            client_id,
            is_active=body.is_active,
            actor=lab_user,
            actor_role=actor.role,
        )
    except ClientNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    session.commit()
    return ClientOut.from_model(client)
