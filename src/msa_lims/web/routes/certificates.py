"""Certificate of Analysis issuance and download.

``GET /api/certificates/{id}/pdf`` is this system's first `GET` endpoint —
every write endpoint before it was POST-only, echoing back only what the
request itself had just supplied. A signed document is the first thing worth
fetching again later.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from msa_lims.certificates.service import CertificateInput, CertificateService, get_pdf
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import CertificateCreate, CertificateOut

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


@router.post("", response_model=CertificateOut, status_code=status.HTTP_201_CREATED)
def create_certificate(
    body: CertificateCreate, session: SessionDep, actor: ActorDep, issued_by: LabUserDep
) -> CertificateOut:
    service = CertificateService(session)
    certificate = service.create(
        CertificateInput(
            client_id=body.client_id,
            sample_ids=tuple(body.sample_ids),
            issued_at=body.issued_at,
            notes=body.notes,
            supersedes_id=body.supersedes_id,
            superseded_reason=body.superseded_reason,
        ),
        issued_by=issued_by,
        actor_role=actor.role,
    )
    session.commit()
    return CertificateOut.from_model(certificate, sample_count=len(body.sample_ids))


@router.get(
    "/{certificate_id}/pdf",
    response_class=Response,
    summary="Download the signed Certificate of Analysis",
)
def download_certificate_pdf(certificate_id: int, session: SessionDep, actor: ActorDep) -> Response:
    certificate, pdf_bytes = get_pdf(session, certificate_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (f'attachment; filename="{certificate.certificate_number}.pdf"'),
            "X-Content-SHA256": certificate.pdf_sha256,
        },
    )
