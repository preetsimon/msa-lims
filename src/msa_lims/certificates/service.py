"""Certificate of Analysis issuance.

A certificate is a signed, append-only document: once issued, its PDF bytes
and the set of results it certifies never change. Correcting one is a new
certificate whose ``supersedes_id`` points at the one it corrects, with a
required reason — never an ``UPDATE`` — matching ``fire_assay_result`` and
``audit_event``.

Unlike ``fire_assay_result``, there is **no "only one current certificate per
client" rule**. A client can hold many independent certificates over time —
one per batch of samples reported — and supersession only guards against a
single chain branching, never against a client having more than one document.

**Issuing a certificate for a sample still at ``ASSAYED`` moves it to
``REPORTED``** — and this is the one Phase 1 write path that actually goes
through :func:`msa_lims.domain.lifecycle.check_transition`'s real,
already-modelled ``ASSAYED -> REPORTED`` transition. Fire assay result
entry's own docstring names this transition as unreachable at the time it was
written, because nothing existed yet to reach it from; this is where it
becomes reachable. Re-certifying an already-``REPORTED`` sample — the
amendment case — leaves its status untouched.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.certificates.pdf import (
    CertificateContent,
    CertifiedElement,
    CertifiedSample,
    render_pdf,
)
from msa_lims.clients.service import ClientNotFoundError
from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import (
    Certificate,
    CertificateMultiElementResult,
    CertificateResult,
    Client,
    FireAssayResult,
    LabUser,
    MultiElementResult,
    Sample,
)
from msa_lims.db.numbering import count_with_prefix, insert_with_unique_number
from msa_lims.domain.enums import MAY_SIGN_CERTIFICATE, Role, SampleStatus
from msa_lims.domain.lifecycle import InsufficientRoleError, check_transition
from msa_lims.domain.values import MeasuredValue
from msa_lims.fire_assay_results.service import current_result, measured_value
from msa_lims.multi_element.service import current_results

#: Three decimal places, the precision already used throughout this
#: codebase's own examples (a 30 g portion, a 0.150 mg bead) and a standard
#: fire-assay reporting convention.
_CERTIFICATE_GRADE_PRECISION = Decimal("0.001")


def _display_grade(measured: MeasuredValue) -> str:
    """Render a computed grade the way a signed document should show it.

    ``fire_assay_result.au_value`` keeps the full precision the domain
    arithmetic produced — correct for recalculation and audit — but a
    division that does not terminate (0.160 mg over 30 g, for instance)
    otherwise prints thirty-four digits of an artifact of ``Decimal``
    division, not thirty-four significant figures any balance ever measured.
    Rounding happens only here, at the point a human reads the number; the
    stored value is untouched.
    """
    if measured.censored:
        return str(measured)
    assert measured.value is not None  # guaranteed by MeasuredValue.__post_init__
    rounded = measured.value.quantize(_CERTIFICATE_GRADE_PRECISION, rounding=ROUND_HALF_EVEN)
    return f"{rounded} {measured.unit.value}"


def _display_element_grade(mer: MultiElementResult) -> str:
    """Render a multi-element grade for the certificate.

    Same rounding discipline as ``_display_grade`` but reads directly from
    the model row rather than reconstructing a MeasuredValue.
    """
    assert mer.grade_value is not None
    rounded = mer.grade_value.quantize(_CERTIFICATE_GRADE_PRECISION, rounding=ROUND_HALF_EVEN)
    return f"{rounded} {mer.grade_unit}"


class CertificateNotFoundError(ValueError):
    """No certificate with this id exists."""


class CertificateCorruptedError(RuntimeError):
    """The stored PDF no longer hashes to what the row claims it does.

    Refusing to serve is the only safe answer here — a client handed a
    silently altered certificate would have no way to know (mirrors QC
    Sentinel's raw-export download, which re-verifies its own content hash on
    every read for the identical reason).
    """


def get_certificate(session: Session, certificate_id: int) -> Certificate:
    """The row, or a clear refusal — the one lookup every read path here shares."""
    certificate = session.get(Certificate, certificate_id)
    if certificate is None:
        raise CertificateNotFoundError(f"no certificate with id {certificate_id}")
    return certificate


def get_pdf(session: Session, certificate_id: int) -> tuple[Certificate, bytes]:
    """The certificate row and its PDF bytes, hash-verified on the way out."""
    certificate = get_certificate(session, certificate_id)

    actual = hashlib.sha256(certificate.pdf_bytes).hexdigest()
    if actual != certificate.pdf_sha256:
        raise CertificateCorruptedError(
            f"certificate {certificate_id} has drifted from its recorded hash"
        )
    return certificate, certificate.pdf_bytes


@dataclass(frozen=True, slots=True)
class CertifiedElementInfo:
    """One element reading frozen into a certificate."""

    element: str
    grade_value: str
    grade_unit: str
    detection_limit: str | None
    digest_method: str


@dataclass(frozen=True, slots=True)
class CertifiedSampleInfo:
    """One sample this certificate covers, read back from ``certificate_result``.

    ``fire_assay_result_id`` and ``grade`` reflect the *specific* result row
    frozen at issuance — see the module docstring — not whatever the sample's
    current result happens to be now. If that result was later superseded,
    this is still what the certificate actually said.

    ``elements`` holds any multi-element readings frozen at the same moment.
    Empty when the sample has no ICP results.
    """

    sample_id: int
    sample_label: str
    fire_assay_result_id: int
    method: str
    grade: MeasuredValue
    elements: tuple[CertifiedElementInfo, ...] = ()
    digest_method: str | None = None


def get_certified_samples(session: Session, certificate_id: int) -> list[CertifiedSampleInfo]:
    """Every sample a certificate covers, in the order they were certified.

    Used by both the issuance response and the detail `GET` — one query, one
    shape, so the two can never show a certificate's contents differently.
    Multi-element results are fetched separately and attached by sample id.
    """
    from msa_lims.db.models import MultiElementResult

    rows = session.execute(
        select(CertificateResult, Sample, FireAssayResult)
        .join(Sample, CertificateResult.sample_id == Sample.id)
        .join(FireAssayResult, CertificateResult.fire_assay_result_id == FireAssayResult.id)
        .where(CertificateResult.certificate_id == certificate_id)
        .order_by(CertificateResult.id)
    ).all()

    # Fetch frozen multi-element results for this certificate.
    element_rows = session.execute(
        select(CertificateMultiElementResult, MultiElementResult)
        .join(
            MultiElementResult,
            CertificateMultiElementResult.multi_element_result_id == MultiElementResult.id,
        )
        .where(CertificateMultiElementResult.certificate_id == certificate_id)
        .order_by(CertificateMultiElementResult.id)
    ).all()

    # Group element results by sample_id.
    elements_by_sample: dict[int, list[CertifiedElementInfo]] = {}
    digest_by_sample: dict[int, str] = {}
    for _cmr, mer in element_rows:
        info = CertifiedElementInfo(
            element=mer.element.value,
            grade_value=str(mer.grade_value),
            grade_unit=mer.grade_unit,
            detection_limit=(
                None if mer.detection_limit is None else str(mer.detection_limit)
            ),
            digest_method=mer.digest_method.value,
        )
        elements_by_sample.setdefault(mer.sample_id, []).append(info)
        digest_by_sample[mer.sample_id] = mer.digest_method.value

    return [
        CertifiedSampleInfo(
            sample_id=sample.id,
            sample_label=sample.sample_id,
            fire_assay_result_id=result.id,
            method=result.method.value,
            grade=measured_value(result),
            elements=tuple(elements_by_sample.get(sample.id, [])),
            digest_method=digest_by_sample.get(sample.id),
        )
        for _certificate_result, sample, result in rows
    ]


class CertificateValidationError(ValueError):
    """One or more problems with a certificate request, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class CertificateInput:
    client_id: int
    sample_ids: tuple[int, ...]
    issued_at: datetime
    notes: str | None = None
    #: Set both to amend an existing certificate; see the module docstring.
    supersedes_id: int | None = None
    superseded_reason: str | None = None


def _is_superseded(session: Session, certificate_id: int) -> bool:
    return (
        session.scalar(select(Certificate.id).where(Certificate.supersedes_id == certificate_id))
        is not None
    )


class CertificateService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, data: CertificateInput, *, issued_by: LabUser, actor_role: Role
    ) -> Certificate:
        if actor_role not in MAY_SIGN_CERTIFICATE:
            raise InsufficientRoleError(
                f"{actor_role.value} may not issue a certificate; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_SIGN_CERTIFICATE))
            )

        client = self._session.get(Client, data.client_id)
        if client is None:
            raise ClientNotFoundError(f"no client with id {data.client_id}")

        problems: list[str] = []

        samples, results = self._resolve_samples(data.sample_ids, client, problems)
        superseded = self._resolve_supersession(data, client, problems)

        if problems:
            raise CertificateValidationError(problems)

        # The one Phase 1 write path that actually goes through the real,
        # already-modelled lifecycle transition -- see the module docstring.
        for sample in samples:
            if sample.status is SampleStatus.ASSAYED:
                check_transition(
                    source=SampleStatus.ASSAYED,
                    target=SampleStatus.REPORTED,
                    sample_type=sample.sample_type,
                    role=actor_role,
                )
                sample.status = SampleStatus.REPORTED

        # The PDF embeds the certificate number, so it is rendered inside the
        # build step: a retried attempt renders a fresh document with its own
        # number, and the stored bytes always match the stored number.
        #
        # Multi-element results are frozen at issuance time: for each sample,
        # the current (un-superseded) element readings are captured into the
        # PDF and into certificate_multi_element_result, so the certificate
        # is a historical snapshot that cannot drift.
        def build_certificate(number: str) -> Certificate:
            certified_samples: list[CertifiedSample] = []
            for sample, result in zip(samples, results, strict=True):
                elements = current_results(self._session, sample.id)
                certified_elements = tuple(
                    CertifiedElement(
                        element=el.element.value,
                        grade_display=_display_element_grade(el),
                    )
                    for el in elements
                )
                digest = elements[0].digest_method.value if elements else None
                certified_samples.append(
                    CertifiedSample(
                        sample_id=sample.sample_id,
                        method=result.method.value,
                        au_display=_display_grade(measured_value(result)),
                        elements=certified_elements,
                        digest_method=digest,
                    )
                )

            pdf_bytes = render_pdf(
                CertificateContent(
                    certificate_number=number,
                    client_name=client.name,
                    issued_at=data.issued_at,
                    issued_by_name=issued_by.full_name,
                    samples=tuple(certified_samples),
                    supersedes_number=superseded.certificate_number if superseded else None,
                    superseded_reason=data.superseded_reason,
                    notes=data.notes,
                )
            )
            return Certificate(
                certificate_number=number,
                client_id=client.id,
                issued_by_id=issued_by.id,
                issued_at=data.issued_at,
                supersedes_id=data.supersedes_id,
                superseded_reason=data.superseded_reason,
                pdf_bytes=pdf_bytes,
                pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
                notes=data.notes,
            )

        # Same savepoint-retry discipline as submission numbering — see
        # db/numbering.py. The certificate row is append-only at the grant
        # level, so the number must be right before the INSERT; there is no
        # UPDATE to fall back on.
        certificate = insert_with_unique_number(
            self._session,
            build_certificate,
            lambda attempt: self._allocate_number(data.issued_at, attempt),
        )

        for sample, result in zip(samples, results, strict=True):
            self._session.add(
                CertificateResult(
                    certificate_id=certificate.id,
                    sample_id=sample.id,
                    fire_assay_result_id=result.id,
                )
            )
            # Freeze the current multi-element readings for this sample.
            for mer in current_results(self._session, sample.id):
                self._session.add(
                    CertificateMultiElementResult(
                        certificate_id=certificate.id,
                        sample_id=sample.id,
                        multi_element_result_id=mer.id,
                    )
                )

        record_audit_event(
            self._session,
            table_name="certificate",
            record_id=certificate.id,
            action="amend" if data.supersedes_id is not None else "create",
            actor_id=issued_by.id,
            after={
                "certificate_number": certificate.certificate_number,
                "sample_count": len(samples),
            },
            reason=data.superseded_reason,
        )

        return certificate

    def _resolve_samples(
        self, sample_ids: tuple[int, ...], client: Client, problems: list[str]
    ) -> tuple[list[Sample], list[FireAssayResult]]:
        if not sample_ids:
            problems.append("a certificate must cover at least one sample")
            return [], []

        samples: list[Sample] = []
        results: list[FireAssayResult] = []
        seen: set[int] = set()

        for sample_id in sample_ids:
            if sample_id in seen:
                problems.append(f"sample id {sample_id} appears more than once")
                continue
            seen.add(sample_id)

            sample = self._session.get(Sample, sample_id)
            if sample is None:
                problems.append(f"no sample with id {sample_id}")
                continue
            if sample.submission.client_id != client.id:
                problems.append(f"sample {sample.sample_id!r} does not belong to {client.name!r}")
                continue

            result = current_result(self._session, sample.id)
            if result is None:
                problems.append(f"sample {sample.sample_id!r} has no fire assay result yet")
                continue

            samples.append(sample)
            results.append(result)

        return samples, results

    def _resolve_supersession(
        self, data: CertificateInput, client: Client, problems: list[str]
    ) -> Certificate | None:
        if data.supersedes_id is None:
            return None

        superseded = self._session.get(Certificate, data.supersedes_id)
        if superseded is None or _is_superseded(self._session, superseded.id):
            problems.append(
                f"certificate #{data.supersedes_id} is not a current certificate that "
                "can be superseded"
            )
        elif superseded.client_id != client.id:
            problems.append(f"certificate #{data.supersedes_id} belongs to a different client")

        if not (data.superseded_reason and data.superseded_reason.strip()):
            problems.append("superseding a certificate requires a reason")

        return superseded

    def _allocate_number(self, issued_at: datetime, attempt: int = 0) -> str:
        """A certificate number in the shape ``COA-2026-0001``.

        Same provisional-numbering caveat as ``submission_number`` — see
        ``submissions/service.py``. The ``attempt`` offset and the savepoint
        retry in :func:`msa_lims.db.numbering.insert_with_unique_number` are
        what make a concurrent collision a recompute instead of a 500.
        """
        prefix = f"COA-{issued_at.year}-"
        count = count_with_prefix(self._session, Certificate, "certificate_number", prefix)
        return f"{prefix}{count + 1 + attempt:04d}"
