"""Submission intake — the first endpoint that writes anything.

Everything the request needs beyond the HTTP shape lives in
:mod:`msa_lims.submissions.service`; this module only translates HTTP into a
service call and a service result back into HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.submissions.service import SampleInput, SubmissionInput, SubmissionService
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import SubmissionCreate, SubmissionOut

router = APIRouter(prefix="/api/submissions", tags=["submissions"])


@router.post("", response_model=SubmissionOut, status_code=status.HTTP_201_CREATED)
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
