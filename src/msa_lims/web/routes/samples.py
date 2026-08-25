"""Sample lookup — read-only, no write path lives here."""

from __future__ import annotations

from fastapi import APIRouter, Query

from msa_lims.domain.enums import SampleStatus
from msa_lims.samples.service import get_sample_detail, list_samples
from msa_lims.web.deps import InternalActorDep, SessionDep
from msa_lims.web.schemas import (
    CertificateReferenceOut,
    FireAssayResultOut,
    SampleDetailOut,
    SampleListItemOut,
)

router = APIRouter(prefix="/api/samples", tags=["samples"])


@router.get("", response_model=list[SampleListItemOut])
def read_samples(
    session: SessionDep,
    actor: InternalActorDep,
    client_id: int | None = Query(default=None),
    status: SampleStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[SampleListItemOut]:
    items = list_samples(session, client_id=client_id, status=status, limit=limit)
    return [
        SampleListItemOut.from_model(
            item.sample, client_name=item.client_name, submission_number=item.submission_number
        )
        for item in items
    ]


@router.get("/{sample_id}", response_model=SampleDetailOut)
def read_sample(sample_id: int, session: SessionDep, actor: InternalActorDep) -> SampleDetailOut:
    detail = get_sample_detail(session, sample_id)
    return SampleDetailOut.from_model(
        detail.sample,
        current_result=(
            FireAssayResultOut.from_model(detail.current_result)
            if detail.current_result is not None
            else None
        ),
        certificates=[
            CertificateReferenceOut(
                id=ref.certificate_id, certificate_number=ref.certificate_number
            )
            for ref in detail.certificates
        ],
    )
