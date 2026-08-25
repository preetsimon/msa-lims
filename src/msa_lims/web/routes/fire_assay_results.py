"""Fire assay result entry — the first write path that computes something.

Every earlier write endpoint stores what it was told. This one calls
:func:`msa_lims.domain.assay.gravimetric_grade` and stores the result of a
calculation, so the number on a future certificate is reproducible from a
weighing, not asserted.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.fire_assay_results.service import FireAssayResultInput, FireAssayResultService
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import FireAssayResultCreate, FireAssayResultOut

router = APIRouter(prefix="/api", tags=["fire-assay-results"])


@router.post(
    "/fire-assay-results", response_model=FireAssayResultOut, status_code=status.HTTP_201_CREATED
)
def create_fire_assay_result(
    body: FireAssayResultCreate, session: SessionDep, actor: ActorDep, analyst: LabUserDep
) -> FireAssayResultOut:
    service = FireAssayResultService(session)
    result = service.create(
        FireAssayResultInput(
            sample_id=body.sample_id,
            gold_bead_mg=body.gold_bead_mg,
            sample_weight_g=body.sample_weight_g,
            balance_sensitivity_mg=body.balance_sensitivity_mg,
            analysed_at=body.analysed_at,
            notes=body.notes,
            supersedes_id=body.supersedes_id,
            superseded_reason=body.superseded_reason,
            crucible_id=body.crucible_id,
        ),
        analyst=analyst,
        actor_role=actor.role,
    )
    session.commit()
    return FireAssayResultOut.from_model(result)
