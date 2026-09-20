"""Prep record entry — the audit trail for sample preparation."""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from msa_lims.db.models import PrepRecord
from msa_lims.domain.enums import PrepStage
from msa_lims.prep_records.service import (
    PrepRecordInput,
    PrepRecordService,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import (
    FORBIDDEN_403,
    SAMPLE_NOT_FOUND,
    merge_responses,
)
from msa_lims.web.schemas import PrepRecordCreate, PrepRecordOut

router = APIRouter(prefix="/api", tags=["prep-records"])

SAMPLE_NOT_FOUND_404 = {**SAMPLE_NOT_FOUND}


@router.get(
    "/samples/{sample_id}/prep-records",
    response_model=list[PrepRecordOut],
    responses=merge_responses(SAMPLE_NOT_FOUND_404),
)
def read_sample_prep_records(
    sample_id: int,
    session: SessionDep,
    actor: InternalActorDep,
) -> list[PrepRecordOut]:
    """All prep records for a sample, newest first."""
    stmt = (
        select(PrepRecord)
        .where(PrepRecord.sample_id == sample_id)
        .order_by(PrepRecord.id.desc())
    )
    records = list(session.scalars(stmt))
    return [PrepRecordOut.from_model(r) for r in records]


@router.post(
    "/prep-records",
    response_model=PrepRecordOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403, SAMPLE_NOT_FOUND_404),
)
def create_prep_record(
    body: PrepRecordCreate,
    session: SessionDep,
    actor: ActorDep,
    lab_user: LabUserDep,
) -> PrepRecordOut:
    service = PrepRecordService(session)
    record = service.create(
        PrepRecordInput(
            sample_id=body.sample_id,
            stage=PrepStage(body.stage),
            instrument_id=body.instrument_id,
            performed_at=body.performed_at,
            input_weight_g=body.input_weight_g,
            output_weight_g=body.output_weight_g,
            supersedes_id=body.supersedes_id,
            superseded_reason=body.superseded_reason,
            notes=body.notes,
        ),
        actor=lab_user,
        actor_role=actor.role,
    )
    session.commit()
    return PrepRecordOut.from_model(record)
