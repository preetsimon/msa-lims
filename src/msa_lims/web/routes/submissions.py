"""Submission intake — the first endpoint that writes anything.

Everything the request needs beyond the HTTP shape lives in
:mod:`msa_lims.submissions.service`; this module only translates HTTP into a
service call and a service result back into HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from msa_lims.submissions.service import (
    SampleInput,
    SubmissionInput,
    SubmissionNotFoundError,
    SubmissionService,
    get_submission,
    list_submissions,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import (
    CLIENT_OR_PROJECT_NOT_FOUND,
    FORBIDDEN_403,
    NOT_FOUND_404,
    merge_responses,
)
from msa_lims.web.schemas import (
    PaginatedResponse,
    SubmissionCreate,
    SubmissionListItemOut,
    SubmissionOut,
)

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


@router.get(
    "",
    response_model=PaginatedResponse[SubmissionListItemOut],
)
def read_submissions(
    session: SessionDep,
    actor: InternalActorDep,
    client_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int | None = Query(default=None),
) -> PaginatedResponse[SubmissionListItemOut]:
    result = list_submissions(
        session,
        client_id=client_id,
        limit=limit,
        cursor=cursor,
    )
    return PaginatedResponse(
        items=[SubmissionListItemOut.from_model(s) for s in result.items],
        next_cursor=result.next_cursor,
    )


@router.get(
    "/{submission_id}",
    response_model=SubmissionOut,
    responses=merge_responses(FORBIDDEN_403, NOT_FOUND_404),
)
def read_submission(
    submission_id: int, session: SessionDep, actor: InternalActorDep
) -> SubmissionOut:
    """A single submission by id, with its samples."""
    try:
        submission = get_submission(session, submission_id)
    except SubmissionNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return SubmissionOut.from_model(submission)


@router.post(
    "",
    response_model=SubmissionOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403, CLIENT_OR_PROJECT_NOT_FOUND),
)
def create_submission(
    body: SubmissionCreate,
    session: SessionDep,
    actor: ActorDep,
    received_by: LabUserDep,
) -> SubmissionOut:
    """Register a work order and the sample rows that arrived with it.

    ``actor`` and ``received_by`` are resolved from the same request and
    normally agree, but the role check reads ``actor.role`` — never
    ``received_by.role`` — so authorisation always reflects what the current
    caller holds right now, not whatever :class:`~msa_lims.db.models.LabUser`
    row last recorded.
    """
    service = SubmissionService(session)
    data = SubmissionInput(
        client_id=body.client_id,
        project_id=body.project_id,
        client_reference=body.client_reference,
        purchase_order=body.purchase_order,
        received_at=body.received_at,
        declared_sample_count=body.declared_sample_count,
        rush=body.rush,
        requested_tat_days=body.requested_tat_days,
        comments=body.comments,
        samples=tuple(
            SampleInput(
                sample_id=sample.sample_id,
                sample_type=sample.sample_type,
                lithology_code=sample.lithology_code,
                alteration_code=sample.alteration_code,
                weight_received_g=sample.weight_received_g,
                easting=sample.easting,
                northing=sample.northing,
                elevation_m=sample.elevation_m,
                comments=sample.comments,
            )
            for sample in body.samples
        ),
    )

    submission = service.create(data, received_by=received_by, actor_role=actor.role)
    session.commit()
    return SubmissionOut.from_model(submission)
