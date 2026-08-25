"""Sample lookup.

Read-only. Every write to a sample happens elsewhere — submission intake
creates it, fire assay result entry and certificate issuance move its
status — this module only answers "what is true about this sample right
now," assembling a detail view from tables that are each somebody else's
concern to write.

:class:`~msa_lims.fire_assay_results.service.SampleNotFoundError` is reused
from ``fire_assay_results/service.py`` rather than redefined here. Unlike
``ClientNotFoundError`` and ``ProjectNotFoundError`` — hoisted to the module
that owns the entity once a second caller needed them — this module already
needs `fire_assay_results.service` for :func:`current_result`, so importing
the exception from the same place adds no new coupling; defining a second,
unrelated ``SampleNotFoundError`` here would only recreate the exact
two-classes-one-name hazard that hoisting was meant to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import (
    Certificate,
    CertificateResult,
    Client,
    FireAssayResult,
    Sample,
    Submission,
)
from msa_lims.domain.enums import SampleStatus
from msa_lims.fire_assay_results.service import SampleNotFoundError, current_result

__all__ = [
    "CertificateReference",
    "SampleDetail",
    "SampleListItem",
    "SampleNotFoundError",
    "get_sample_detail",
    "list_samples",
]


@dataclass(frozen=True, slots=True)
class CertificateReference:
    certificate_id: int
    certificate_number: str


@dataclass(frozen=True, slots=True)
class SampleDetail:
    sample: Sample
    current_result: FireAssayResult | None
    certificates: tuple[CertificateReference, ...]


def get_sample_detail(session: Session, sample_id: int) -> SampleDetail:
    """A sample, its current result if any, and every certificate that names it.

    "Certificates that name it" comes from ``certificate_result``, not from
    walking the sample's status — a sample can be ``REPORTED`` with its
    certificate later superseded, and the honest answer to "which documents
    mention this sample" is a join, not an inference from one enum value.
    """
    sample = session.get(Sample, sample_id)
    if sample is None:
        raise SampleNotFoundError(f"no sample with id {sample_id}")

    result = current_result(session, sample.id)

    rows = session.execute(
        select(Certificate.id, Certificate.certificate_number)
        .join(CertificateResult, CertificateResult.certificate_id == Certificate.id)
        .where(CertificateResult.sample_id == sample.id)
        .order_by(Certificate.id)
    ).all()
    certificates = tuple(
        CertificateReference(certificate_id=row.id, certificate_number=row.certificate_number)
        for row in rows
    )

    return SampleDetail(sample=sample, current_result=result, certificates=certificates)


@dataclass(frozen=True, slots=True)
class SampleListItem:
    sample: Sample
    client_name: str
    submission_number: str


def list_samples(
    session: Session,
    *,
    client_id: int | None = None,
    status: SampleStatus | None = None,
    limit: int = 100,
) -> list[SampleListItem]:
    """The most recent samples, newest first, one query — no per-row grade
    lookup, deliberately: a list of a hundred samples fetching each one's
    current result would be a hundred extra queries for a fact the detail
    screen already shows. ``client_id`` and ``status`` are exposed for future
    filtering; nothing calls them with a value yet.
    """
    stmt = (
        select(Sample, Client.name, Submission.submission_number)
        .join(Submission, Sample.submission_id == Submission.id)
        .join(Client, Submission.client_id == Client.id)
        .order_by(Sample.id.desc())
        .limit(limit)
    )
    if client_id is not None:
        stmt = stmt.where(Submission.client_id == client_id)
    if status is not None:
        stmt = stmt.where(Sample.status == status)

    rows = session.execute(stmt).all()
    return [
        SampleListItem(sample=sample, client_name=client_name, submission_number=submission_number)
        for sample, client_name, submission_number in rows
    ]
