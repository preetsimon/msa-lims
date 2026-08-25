"""Certificate of Analysis issuance, lookup, and download.

``GET /api/certificates/{id}/pdf`` was this system's first `GET` endpoint —
every write endpoint before it was POST-only, echoing back only what the
request itself had just supplied. ``GET /api/certificates/{id}`` is its
metadata counterpart, sharing the exact same certified-samples query the
issuance response uses, so the two can never describe one certificate's
contents differently.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy.orm import Session

from msa_lims.certificates.service import (
    CertificateInput,
    CertificateService,
    get_certificate,
    get_certified_samples,
    get_pdf,
)
from msa_lims.db.models import Certificate
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import (
    CertificateCreate,
    CertificateOut,
    CertifiedSampleOut,
    MeasuredValueOut,
)

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


def _certificate_out(session: Session, certificate: Certificate) -> CertificateOut:
    samples = [
        CertifiedSampleOut(
            sample_id=info.sample_id,
            sample_label=info.sample_label,
            fire_assay_result_id=info.fire_assay_result_id,
            method=info.method,
            au=MeasuredValueOut.from_domain(info.grade),
        )
        for info in get_certified_samples(session, certificate.id)
    ]
    return CertificateOut.from_model(certificate, samples=samples)


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
    return _certificate_out(session, certificate)


@router.get("/{certificate_id}", response_model=CertificateOut)
def read_certificate(certificate_id: int, session: SessionDep, actor: ActorDep) -> CertificateOut:
    certificate = get_certificate(session, certificate_id)
    return _certificate_out(session, certificate)


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
