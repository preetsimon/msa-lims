"""Multi-element ICP result entry — bulk import of element concentrations.

One endpoint accepts a full ICP run's worth of elements for a sample, following
the same append-only, role-gated pattern as fire assay result entry. The
import is atomic: all elements pass or all fail.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.domain.enums import DigestMethod, Element
from msa_lims.multi_element.service import (
    MultiElementImportInput,
    MultiElementService,
)
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import (
    ElementResultCreate,
    MultiElementImportCreate,
    MultiElementImportOut,
    MultiElementResultOut,
)

router = APIRouter(prefix="/api", tags=["multi-element-results"])


@router.post(
    "/samples/{sample_id}/multi-element-results",
    response_model=MultiElementImportOut,
    status_code=status.HTTP_201_CREATED,
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
        from fastapi import HTTPException

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
            results=[
                _to_domain(r) for r in body.results
            ],
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
)
def list_multi_element_results(
    sample_id: int,
    session: SessionDep,
    actor: ActorDep,
) -> list[MultiElementResultOut]:
    """Read the current (un-superseded) element results for a sample."""
    from msa_lims.multi_element.service import current_results

    rows = current_results(session, sample_id)
    return [MultiElementResultOut.from_model(r) for r in rows]


def _to_domain(schema: ElementResultCreate):  # type: ignore[no-untyped-def]
    from msa_lims.multi_element.service import ElementResult

    return ElementResult(
        element=Element(schema.element),
        grade_value=schema.grade_value,
        grade_unit=schema.grade_unit,
        detection_limit=schema.detection_limit,
    )
