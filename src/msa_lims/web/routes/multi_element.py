"""Multi-element ICP result entry — bulk import of element concentrations.

One endpoint accepts a full ICP run's worth of elements for a sample, following
the same append-only, role-gated pattern as fire assay result entry. The
import is atomic: all elements pass or all fail.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from msa_lims.domain.enums import DigestMethod, Element
from msa_lims.domain.units import Unit
from msa_lims.multi_element.service import (
    ElementResult,
    MultiElementImportInput,
    MultiElementService,
    current_results,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import (
    FORBIDDEN_403,
    NOT_FOUND_404,
    SAMPLE_NOT_FOUND,
    merge_responses,
)
from msa_lims.web.schemas import (
    ElementResultCreate,
    MultiElementImportCreate,
    MultiElementImportOut,
    MultiElementResultOut,
    MultiElementSupersedeCreate,
)

router = APIRouter(prefix="/api", tags=["multi-element-results"])


@router.post(
    "/samples/{sample_id}/multi-element-results",
    response_model=MultiElementImportOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403, SAMPLE_NOT_FOUND, NOT_FOUND_404),
)
def import_multi_element_results(
    sample_id: int,
    body: MultiElementImportCreate,
    session: SessionDep,
    actor: ActorDep,
    analyst: LabUserDep,
) -> MultiElementImportOut:
    """Bulk-import one ICP run's worth of element results for a sample.

    The request carries the sample id both in the URL and in the body — the
    URL is the RESTful anchor, the body is what the service reads — and the
    two must agree.
    """
    if body.sample_id != sample_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"sample_id in URL ({sample_id}) does not match "
                f"sample_id in body ({body.sample_id})"
            ),
        )

    service = MultiElementService(session)
    results = service.import_results(
        MultiElementImportInput(
            sample_id=sample_id,
            digest_method=DigestMethod(body.digest_method),
            method_notes=body.method_notes,
            analysed_at=body.analysed_at,
            results=[_to_domain(r) for r in body.results],
        ),
        analyst=analyst,
        actor_role=actor.role,
    )
    session.commit()

    return MultiElementImportOut(
        sample_id=sample_id,
        digest_method=body.digest_method,
        analysed_at=body.analysed_at,
        imported=[MultiElementResultOut.from_model(r) for r in results],
    )


@router.get(
    "/samples/{sample_id}/multi-element-results",
    response_model=list[MultiElementResultOut],
    responses=merge_responses(SAMPLE_NOT_FOUND),
)
def list_multi_element_results(
    sample_id: int,
    session: SessionDep,
    actor: InternalActorDep,
) -> list[MultiElementResultOut]:
    """Read the current (un-superseded) element results for a sample."""
    rows = current_results(session, sample_id)
    return [MultiElementResultOut.from_model(r) for r in rows]


@router.patch(
    "/samples/{sample_id}/multi-element-results/{element}",
    response_model=MultiElementResultOut,
    responses=merge_responses(FORBIDDEN_403, SAMPLE_NOT_FOUND, NOT_FOUND_404),
)
def supersede_multi_element_result(
    sample_id: int,
    element: str,
    body: MultiElementSupersedeCreate,
    session: SessionDep,
    actor: ActorDep,
    analyst: LabUserDep,
) -> MultiElementResultOut:
    """Correct a single element reading with a new row in the chain."""
    try:
        element_enum = Element(element)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown element {element!r}",
        ) from exc

    service = MultiElementService(session)
    result = service.supersede(
        sample_id=sample_id,
        element=element_enum,
        digest_method=DigestMethod(body.digest_method),
        new_value=body.grade_value,
        new_unit=Unit(body.grade_unit),
        detection_limit=body.detection_limit,
        analysed_at=body.analysed_at,
        method_notes=body.method_notes,
        reason=body.reason,
        analyst=analyst,
        actor_role=actor.role,
    )
    session.commit()
    return MultiElementResultOut.from_model(result)


def _to_domain(schema: ElementResultCreate) -> ElementResult:
    return ElementResult(
        element=Element(schema.element),
        grade_value=schema.grade_value,
        grade_unit=Unit(schema.grade_unit),
        detection_limit=schema.detection_limit,
    )
