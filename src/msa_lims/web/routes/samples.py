"""Sample lookup — read-only, no write path lives here."""

from __future__ import annotations

from fastapi import APIRouter

from msa_lims.samples.service import get_sample_detail
from msa_lims.web.deps import ActorDep, SessionDep
from msa_lims.web.schemas import CertificateReferenceOut, FireAssayResultOut, SampleDetailOut

router = APIRouter(prefix="/api/samples", tags=["samples"])


@router.get("/{sample_id}", response_model=SampleDetailOut)
def read_sample(sample_id: int, session: SessionDep, actor: ActorDep) -> SampleDetailOut:
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
