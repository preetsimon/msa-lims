"""Sample lookup — read-only, no write path lives here."""

from __future__ import annotations

from fastapi import APIRouter, Query

from msa_lims.domain.enums import SampleStatus
from msa_lims.provenance.service import get_sample_provenance, seal, seal_payload
from msa_lims.samples.service import get_sample_detail, list_samples
from msa_lims.web.deps import InternalActorDep, SessionDep
from msa_lims.web.routes.error_responses import SAMPLE_NOT_FOUND, merge_responses
from msa_lims.web.schemas import (
    CertificateReferenceOut,
    CrucibleReferenceOut,
    FireAssayResultOut,
    PaginatedResponse,
    ProvenanceOut,
    SampleDetailOut,
    SampleListItemOut,
)

router = APIRouter(prefix="/api/samples", tags=["samples"])


@router.get(
    "",
    response_model=PaginatedResponse[SampleListItemOut],
    responses=merge_responses(SAMPLE_NOT_FOUND),
)
def read_samples(
    session: SessionDep,
    actor: InternalActorDep,
    client_id: int | None = Query(default=None),
    status: SampleStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: int | None = Query(default=None),
) -> PaginatedResponse[SampleListItemOut]:
    result = list_samples(
        session,
        client_id=client_id,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return PaginatedResponse(
        items=[
            SampleListItemOut.from_model(
                item.sample,
                client_name=item.client_name,
                submission_number=item.submission_number,
            )
            for item in result.items
        ],
        next_cursor=result.next_cursor,
    )


@router.get(
    "/{sample_id}",
    response_model=SampleDetailOut,
    responses=merge_responses(SAMPLE_NOT_FOUND),
)
def read_sample(sample_id: int, session: SessionDep, actor: InternalActorDep) -> SampleDetailOut:
    detail = get_sample_detail(session, sample_id)
    return SampleDetailOut.from_model(
        detail.sample,
        current_result=(
            FireAssayResultOut.from_model(detail.current_result)
            if detail.current_result is not None
            else None
        ),
        result_history=[
            FireAssayResultOut.from_model(r) for r in detail.result_history
        ],
        crucible=(
            CrucibleReferenceOut(
                id=detail.crucible.crucible_id,
                batch_id=detail.crucible.batch_id,
                position_row=detail.crucible.position_row,
                position_col=detail.crucible.position_col,
                status=detail.crucible.status,
                charged_at=detail.crucible.charged_at,
            )
            if detail.crucible is not None
            else None
        ),
        certificates=[
            CertificateReferenceOut(
                id=ref.certificate_id, certificate_number=ref.certificate_number
            )
            for ref in detail.certificates
        ],
    )


@router.get(
    "/{sample_id}/provenance",
    response_model=ProvenanceOut,
    responses=merge_responses(SAMPLE_NOT_FOUND),
)
def read_sample_provenance(
    sample_id: int, session: SessionDep, actor: InternalActorDep
) -> ProvenanceOut:
    """This sample's whole evidence dossier — audit idea #3.

    Assembled read-only from rows that already existed and sealed with a
    `sha256` a recipient can recompute offline. Nested under the sample
    rather than given its own top-level path because a dossier is not an
    entity in its own right: it is one view of one sample, the same way
    `/api/certificates/{id}/pdf` is one rendering of one certificate.
    """
    provenance = get_sample_provenance(session, sample_id)
    return ProvenanceOut.from_payload(seal_payload(provenance), seal=seal(provenance))
