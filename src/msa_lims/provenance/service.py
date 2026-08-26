"""One sample's whole evidence dossier, assembled and sealed.

Audit idea #3, "Provenance as a Product." Every fact this dossier reports
was already in the database — submission and intake, the furnace charge and
its position, the parting and weighing measurements, the full result chain
including superseded rows, the certificates and their PDF hashes, and the
audit events behind all of it. What did not exist was any way to read it
back *as one narrative*: the sample detail view answers "what is true now,"
which is a different (and much smaller) question than "how do you know."

**Read-only. No new writes, therefore no new risk surface** — the sketch's
own words, and the reason this could be built directly on top of the
append-only design rather than alongside it. The dossier is only possible
because nothing in this system overwrites: a corrected result did not
replace the original, so both are here, in order, with the stated reason
for the correction.

The dossier carries a ``seal``: ``sha256`` over the
:func:`~msa_lims.domain.canonical.canonical_json` rendering of everything
else in it. That makes the bundle self-checking — a recipient who holds the
JSON can recompute the seal without asking this server anything, exactly
the way `certificates/service.py` re-verifies a stored PDF against its own
hash on every download. It is *not* a signature: it proves the bundle is
internally consistent, not that this lab authored it. Binding a dossier to
a signing key is audit idea #2's separate scope.

Deliberately **not** included: anything requiring a new column or a new
write path. The prep-record gap (no `PrepRecord` exists yet — see
PROGRESS.md) means the prep stage shows only as the sample's own status
transitions, which is honestly all this system currently records about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, select, tuple_
from sqlalchemy.orm import Session, aliased

from msa_lims.db.models import (
    AuditEvent,
    Batch,
    Certificate,
    CertificateResult,
    Client,
    Crucible,
    DrillHole,
    FireAssayResult,
    FluxRecipe,
    LabUser,
    Project,
    Sample,
    Submission,
)
from msa_lims.domain.canonical import canonical_sha256
from msa_lims.fire_assay_results.service import SampleNotFoundError

__all__ = [
    "AuditEntry",
    "CertificateRecord",
    "CrucibleRecord",
    "ResultRecord",
    "SampleProvenance",
    "get_sample_provenance",
]


@dataclass(frozen=True, slots=True)
class CrucibleRecord:
    """One furnace charge this sample went through, with the batch around it.

    A sample can have more than one: a re-assay charges it into a *new*
    batch rather than rewinding the old one (see
    ``domain/batch_lifecycle.py``), so the list is the physical history, not
    a single current position.
    """

    crucible: Crucible
    batch: Batch
    flux_recipe_name: str


@dataclass(frozen=True, slots=True)
class ResultRecord:
    result: FireAssayResult
    analyst_name: str | None


@dataclass(frozen=True, slots=True)
class CertificateRecord:
    certificate: Certificate
    #: The *specific* result this certificate froze for this sample, which
    #: is not necessarily the sample's current one — see ``CertificateResult``.
    certified_result_id: int
    issued_by_name: str | None


@dataclass(frozen=True, slots=True)
class AuditEntry:
    event: AuditEvent
    actor_name: str | None


@dataclass(frozen=True, slots=True)
class SampleProvenance:
    sample: Sample
    submission: Submission
    client: Client
    project: Project | None
    drill_hole: DrillHole | None
    received_by_name: str | None
    crucibles: tuple[CrucibleRecord, ...]
    results: tuple[ResultRecord, ...]
    certificates: tuple[CertificateRecord, ...]
    audit_entries: tuple[AuditEntry, ...]


def _decimal(value: Decimal | None) -> str | None:
    """Decimals render as their exact string, never as a float.

    ``float(Decimal("0.1"))`` is not 0.1, and a seal computed over a float
    would depend on the platform's binary rounding — the same reason
    ``domain/units.py`` refuses to hand out floats at all.
    """
    return None if value is None else str(value)


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def get_sample_provenance(session: Session, sample_id: int) -> SampleProvenance:
    """Assemble one sample's full dossier.

    Several small queries rather than one enormous join: the pieces are
    genuinely different shapes (one submission, N crucibles, N results, N
    certificates, N audit rows), and a single join would multiply rows
    across those dimensions and need de-duplicating in Python anyway. This
    is a detail view for one sample, not a listing — the per-row query cost
    ``list_samples`` refuses to pay does not apply here.
    """
    sample = session.get(Sample, sample_id)
    if sample is None:
        raise SampleNotFoundError(f"no sample with id {sample_id}")

    submission = session.get(Submission, sample.submission_id)
    if submission is None:  # pragma: no cover - FK guarantees this
        raise SampleNotFoundError(f"sample {sample_id} names a submission that does not exist")

    client = session.get(Client, submission.client_id)
    if client is None:  # pragma: no cover - FK guarantees this
        raise SampleNotFoundError(f"sample {sample_id} names a client that does not exist")

    project = (
        session.get(Project, submission.project_id) if submission.project_id is not None else None
    )
    drill_hole = (
        session.get(DrillHole, sample.drill_hole_id) if sample.drill_hole_id is not None else None
    )
    received_by = (
        session.get(LabUser, submission.received_by_id)
        if submission.received_by_id is not None
        else None
    )

    crucibles = tuple(
        CrucibleRecord(crucible=crucible, batch=batch, flux_recipe_name=recipe_name)
        for crucible, batch, recipe_name in session.execute(
            select(Crucible, Batch, FluxRecipe.name)
            .join(Batch, Crucible.batch_id == Batch.id)
            .join(FluxRecipe, Crucible.flux_recipe_id == FluxRecipe.id)
            .where(Crucible.sample_id == sample.id)
            .order_by(Crucible.id)
        ).all()
    )

    analyst = aliased(LabUser)
    results = tuple(
        ResultRecord(result=result, analyst_name=analyst_name)
        for result, analyst_name in session.execute(
            select(FireAssayResult, analyst.full_name)
            .outerjoin(analyst, FireAssayResult.analyst_id == analyst.id)
            .where(FireAssayResult.sample_id == sample.id)
            .order_by(FireAssayResult.id)
        ).all()
    )

    issuer = aliased(LabUser)
    certificates = tuple(
        CertificateRecord(
            certificate=certificate,
            certified_result_id=certified_result_id,
            issued_by_name=issued_by_name,
        )
        for certificate, certified_result_id, issued_by_name in session.execute(
            select(Certificate, CertificateResult.fire_assay_result_id, issuer.full_name)
            .join(CertificateResult, CertificateResult.certificate_id == Certificate.id)
            .outerjoin(issuer, Certificate.issued_by_id == issuer.id)
            .where(CertificateResult.sample_id == sample.id)
            .order_by(Certificate.id)
        ).all()
    )

    audit_entries = tuple(
        AuditEntry(event=event, actor_name=actor_name)
        for event, actor_name in session.execute(
            _audit_events_for(
                sample=sample,
                submission=submission,
                crucibles=crucibles,
                results=results,
                certificates=certificates,
            )
        ).all()
    )

    return SampleProvenance(
        sample=sample,
        submission=submission,
        client=client,
        project=project,
        drill_hole=drill_hole,
        received_by_name=received_by.full_name if received_by is not None else None,
        crucibles=crucibles,
        results=results,
        certificates=certificates,
        audit_entries=audit_entries,
    )


def _audit_events_for(
    *,
    sample: Sample,
    submission: Submission,
    crucibles: tuple[CrucibleRecord, ...],
    results: tuple[ResultRecord, ...],
    certificates: tuple[CertificateRecord, ...],
) -> Select[tuple[AuditEvent, str]]:
    """Every audit row touching any record in this sample's own story.

    Keyed on the ``(table_name, record_id)`` pairs the dossier already
    resolved, as one composite ``IN`` — the same grain
    ``ix_audit_event_target`` indexes, so this stays one indexed lookup
    rather than a scan per related record. Ordered by ``id``, which is the
    chain's own order (see ``db/audit.py``) and therefore the order the
    events actually happened in.

    The return annotation says ``str`` for the name because that is what
    SQLAlchemy's own typing infers from the column; the outer join means
    the runtime value is genuinely ``str | None`` (a system action has no
    human actor — see ``AuditEvent.actor_id``), which is what
    :class:`AuditEntry` declares and what callers must handle.
    """
    targets: list[tuple[str, int]] = [
        ("sample", sample.id),
        ("submission", submission.id),
    ]
    targets.extend(("crucible", record.crucible.id) for record in crucibles)
    targets.extend(("batch", record.batch.id) for record in crucibles)
    targets.extend(("fire_assay_result", record.result.id) for record in results)
    targets.extend(("certificate", record.certificate.id) for record in certificates)

    actor = aliased(LabUser)
    return (
        select(AuditEvent, actor.full_name)
        .outerjoin(actor, AuditEvent.actor_id == actor.id)
        .where(tuple_(AuditEvent.table_name, AuditEvent.record_id).in_(targets))
        .order_by(AuditEvent.id)
    )


def seal_payload(provenance: SampleProvenance) -> dict[str, object]:
    """The dossier as plain data — the exact structure the seal covers.

    Kept here rather than in ``web/schemas.py`` on purpose: the seal must be
    computable without FastAPI, so a verifier (or a future export job) can
    reproduce it from the same rows. The web layer renders *this* structure
    rather than assembling its own, so the sealed bytes and the bytes a
    client receives can never describe different things.
    """
    return {
        "sample": {
            "id": provenance.sample.id,
            "sample_id": provenance.sample.sample_id,
            "sample_type": provenance.sample.sample_type.value,
            "status": provenance.sample.status.value,
            "from_depth_m": _decimal(provenance.sample.from_depth_m),
            "to_depth_m": _decimal(provenance.sample.to_depth_m),
            "weight_received_g": _decimal(provenance.sample.weight_received_g),
        },
        "submission": {
            "id": provenance.submission.id,
            "submission_number": provenance.submission.submission_number,
            "received_at": _timestamp(provenance.submission.received_at),
            "received_by": provenance.received_by_name,
            "client_reference": provenance.submission.client_reference,
        },
        "client": {"id": provenance.client.id, "code": provenance.client.code},
        "project": None if provenance.project is None else {"name": provenance.project.name},
        "drill_hole": (
            None if provenance.drill_hole is None else {"hole_id": provenance.drill_hole.hole_id}
        ),
        "crucibles": [
            {
                "id": record.crucible.id,
                "batch_number": record.batch.batch_number,
                "position": f"{record.crucible.position_row}-{record.crucible.position_col}",
                "status": record.crucible.status.value,
                "flux_recipe": record.flux_recipe_name,
                "sample_weight_g": _decimal(record.crucible.sample_weight_g),
                "charged_at": _timestamp(record.crucible.charged_at),
                "lead_button_weight_mg": _decimal(record.crucible.lead_button_weight_mg),
                "prill_weight_mg": _decimal(record.crucible.prill_weight_mg),
                "parting_acid_volume_ml": _decimal(record.crucible.parting_acid_volume_ml),
                "parted_at": _timestamp(record.crucible.parted_at),
                "gold_bead_mg": _decimal(record.crucible.gold_bead_mg),
                "weighed_at": _timestamp(record.crucible.weighed_at),
            }
            for record in provenance.crucibles
        ],
        "results": [
            {
                "id": record.result.id,
                "method": record.result.method.value,
                "gold_bead_mg": _decimal(record.result.gold_bead_mg),
                "sample_weight_g": _decimal(record.result.sample_weight_g),
                "au_value": _decimal(record.result.au_value),
                "au_detection_limit": _decimal(record.result.au_detection_limit),
                "au_censored": record.result.au_censored,
                "au_unit": record.result.au_unit,
                "analysed_at": _timestamp(record.result.analysed_at),
                "analyst": record.analyst_name,
                "crucible_id": record.result.crucible_id,
                "supersedes_id": record.result.supersedes_id,
                "superseded_reason": record.result.superseded_reason,
            }
            for record in provenance.results
        ],
        "certificates": [
            {
                "id": record.certificate.id,
                "certificate_number": record.certificate.certificate_number,
                "issued_at": _timestamp(record.certificate.issued_at),
                "issued_by": record.issued_by_name,
                "pdf_sha256": record.certificate.pdf_sha256,
                "certified_result_id": record.certified_result_id,
                "supersedes_id": record.certificate.supersedes_id,
                "superseded_reason": record.certificate.superseded_reason,
            }
            for record in provenance.certificates
        ],
        "audit_entries": [
            {
                "id": entry.event.id,
                "table_name": entry.event.table_name,
                "record_id": entry.event.record_id,
                "action": entry.event.action,
                "before": entry.event.before,
                "after": entry.event.after,
                "reason": entry.event.reason,
                "actor": entry.actor_name,
                "recorded_at": _timestamp(entry.event.created_at),
                "entry_hash": entry.event.entry_hash,
            }
            for entry in provenance.audit_entries
        ],
    }


def seal(provenance: SampleProvenance) -> str:
    """``sha256`` over the canonical rendering of :func:`seal_payload`.

    Recomputable by anyone holding the dossier: canonicalise the same
    structure, hash it, compare. Deliberately covers the audit rows'
    ``entry_hash`` values too, so a dossier is also a statement about where
    each fact sits in the audit chain (idea #1) — check the chain itself
    via ``GET /api/audit/verify``.
    """
    return canonical_sha256(seal_payload(provenance))
