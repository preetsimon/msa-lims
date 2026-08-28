"""Fire assay result entry — the first write path that computes something.

Every earlier write endpoint stores what it was told. These call into
:mod:`msa_lims.domain.assay` and store the result of a calculation, so the
number on a future certificate is reproducible from the bench record, not
asserted.

**Two endpoints, one for each finish.** ``/fire-assay-results`` takes a
weighed bead; ``/fire-assay-results/solution-finish`` takes a concentration
read off an AAS or ICP-MS. They are separate because the measurements have
nothing in common — a single endpoint would need a ``method`` discriminator
and a request shape where most fields are conditionally required, which is
the shape that lets a bead weight be posted against an ICP method and
validated by nobody. What they produce goes to one table and one supersession
chain; see the service module docstring.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.domain.enums import AssayMethod
from msa_lims.domain.units import Unit
from msa_lims.fire_assay_results.service import (
    FireAssayResultInput,
    FireAssayResultService,
    SolutionFinishInput,
)
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import FireAssayResultCreate, FireAssayResultOut, SolutionFinishCreate

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
            dore_bead_mg=body.dore_bead_mg,
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


@router.post(
    "/fire-assay-results/solution-finish",
    response_model=FireAssayResultOut,
    status_code=status.HTTP_201_CREATED,
)
def create_solution_finish(
    body: SolutionFinishCreate, session: SessionDep, actor: ActorDep, analyst: LabUserDep
) -> FireAssayResultOut:
    service = FireAssayResultService(session)
    result = service.create_solution_finish(
        SolutionFinishInput(
            sample_id=body.sample_id,
            method=AssayMethod(body.method),
            concentration=body.concentration,
            concentration_unit=Unit(body.concentration_unit),
            solution_volume_ml=body.solution_volume_ml,
            sample_weight_g=body.sample_weight_g,
            analysed_at=body.analysed_at,
            detection_limit=body.detection_limit,
            upper_calibration_limit=body.upper_calibration_limit,
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
