"""Bare sample lifecycle moves — see ``sample_lifecycle/service.py``."""

from __future__ import annotations

from fastapi import APIRouter

from msa_lims.domain.enums import SampleStatus
from msa_lims.sample_lifecycle.service import SampleLifecycleService
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import (
    CONFLICT_409,
    FORBIDDEN_403,
    SAMPLE_NOT_FOUND,
    merge_responses,
)
from msa_lims.web.schemas import SampleOut, SampleStatusUpdate

router = APIRouter(prefix="/api/samples", tags=["sample-lifecycle"])


@router.patch(
    "/{sample_id}/status",
    response_model=SampleOut,
    responses=merge_responses(FORBIDDEN_403, SAMPLE_NOT_FOUND, CONFLICT_409),
)
def advance_sample_status(
    sample_id: int,
    body: SampleStatusUpdate,
    session: SessionDep,
    actor: ActorDep,
    advanced_by: LabUserDep,
) -> SampleOut:
    service = SampleLifecycleService(session)
    sample = service.advance(
        sample_id,
        target=SampleStatus(body.target),
        reason=body.reason,
        actor=advanced_by,
        actor_role=actor.role,
    )
    session.commit()
    return SampleOut.from_model(sample)
