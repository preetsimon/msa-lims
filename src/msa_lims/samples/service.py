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
    Crucible,
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
class CrucibleReference:
    crucible_id: int
    batch_id: int
    position_row: int
    position_col: int
    status: str
    charged_at: str


@dataclass(frozen=True, slots=True)
class SampleDetail:
    sample: Sample
    current_result: FireAssayResult | None
    result_history: tuple[FireAssayResult, ...]
    crucible: CrucibleReference | None
    certificates: tuple[CertificateReference, ...]


def get_sample_detail(session: Session, sample_id: int) -> SampleDetail:
    """A sample, its current result if any, its full supersession chain,
    and every certificate that names it.

    "Certificates that name it" comes from ``certificate_result``, not from
    walking the sample's status — a sample can be ``REPORTED`` with its
    certificate later superseded, and the honest answer to "which documents
    mention this sample" is a join, not an inference from one enum value.

    ``result_history`` is every fire assay result for this sample, ordered
    newest-first (current result first, then the result it superseded, then
    the one before that, etc.).  The chain is walked by following
    ``supersedes_id`` links from the current head.
    """
    sample = session.get(Sample, sample_id)
    if sample is None:
        raise SampleNotFoundError(f"no sample with id {sample_id}")

    result = current_result(session, sample.id)

    # Walk the supersession chain from current head backwards.
    history: list[FireAssayResult] = []
    if result is not None:
        current: FireAssayResult | None = result
        while current is not None:
            history.append(current)
            if current.supersedes_id:
                current = session.get(FireAssayResult, current.supersedes_id)
            else:
                current = None

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

    crucible_ref: CrucibleReference | None = None
    if result is not None and result.crucible_id is not None:
        crucible = session.get(Crucible, result.crucible_id)
        if crucible is not None:
            crucible_ref = CrucibleReference(
                crucible_id=crucible.id,
                batch_id=crucible.batch_id,
                position_row=crucible.position_row,
                position_col=crucible.position_col,
                status=crucible.status.value,
                charged_at=crucible.charged_at.isoformat(),
            )

    return SampleDetail(
        sample=sample,
        current_result=result,
        result_history=tuple(history),
        crucible=crucible_ref,
        certificates=certificates,
    )


@dataclass(frozen=True, slots=True)
class SampleListItem:
    sample: Sample
    client_name: str
    submission_number: str


@dataclass(frozen=True, slots=True)
class PaginatedSamples:
    """Cursor-paginated sample list."""

    items: list[SampleListItem]
    next_cursor: int | None


def list_samples(
    session: Session,
    *,
    client_id: int | None = None,
    status: SampleStatus | None = None,
    limit: int = 100,
    cursor: int | None = None,
) -> PaginatedSamples:
    """The most recent samples, newest first, one query — no per-row grade
    lookup, deliberately: a list of a hundred samples fetching each one's
    current result would be a hundred extra queries for a fact the detail
    screen already shows. ``client_id`` and ``status`` are exposed for future
    filtering; nothing calls them with a value yet.

    Cursor-based pagination: pass the ``id`` of the last item from the
    previous page as ``cursor``. Results are returned newest-first, so the
    cursor is a *less-than* bound on ``Sample.id``.
    """
    stmt = (
        select(Sample, Client.name, Submission.submission_number)
        .join(Submission, Sample.submission_id == Submission.id)
        .join(Client, Submission.client_id == Client.id)
        .order_by(Sample.id.desc())
    )
    if client_id is not None:
        stmt = stmt.where(Submission.client_id == client_id)
    if status is not None:
        stmt = stmt.where(Sample.status == status)
    if cursor is not None:
        stmt = stmt.where(Sample.id < cursor)

    rows = session.execute(stmt.limit(limit + 1)).all()
    has_next = len(rows) > limit
    items = [
        SampleListItem(sample=sample, client_name=client_name, submission_number=submission_number)
        for sample, client_name, submission_number in rows[:limit]
    ]
    next_cursor = items[-1].sample.id if has_next and items else None
    return PaginatedSamples(items=items, next_cursor=next_cursor)
