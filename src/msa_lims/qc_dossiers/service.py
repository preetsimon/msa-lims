"""The sealed QC dossier of one completed batch — audit idea #5's contract.

Between this system and QC Sentinel sits an agreement neither can quietly
change: what a batch's quality-control facts are. This module is the LIMS
side of it. When a batch has completed its furnace run, its QC insertions —
the CRMs and blanks charged beside client samples — are assembled into one
canonical document, sealed with a sha256 any recipient can recompute, stored
in the content-addressed blob store, and pointed at from the batch row.

**Read-triggered, idempotent, and honest about it.** :func:`build_qc_dossier`
computes the dossier from rows that already exist; :func:`persist_dossier`
stores those exact bytes and moves the batch's pointer to them. Fetch twice
without new facts and you get the same address back — no second copy, no
second audit event. New measurements mean genuinely different evidence, so a
new address: the old blob stays reachable forever (append-only store), the
same way a superseded result does.

**Advisory, always.** Each entry may carry z-scores and threshold flags
computed by :mod:`msa_lims.domain.qc`, but no verdict is recorded anywhere —
judging is Sentinel's job on export. The dossier is the *evidence* half of
that handshake, which is why its seal covers the measurements and nothing
that looks like a conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.batches.service import BatchNotFoundError
from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import Batch, Crucible, QcMaterial
from msa_lims.domain.assay import AssayCalculationError, gravimetric_grade
from msa_lims.domain.canonical import canonical_json, canonical_sha256
from msa_lims.domain.enums import BatchStatus, QcMaterialType
from msa_lims.domain.lifecycle import TransitionNotAllowedError
from msa_lims.domain.qc import Advisory, blank_advisory, crm_z_score
from msa_lims.domain.values import MeasuredValue
from msa_lims.storage.blob import ensure_blob

__all__ = [
    "QcDossier",
    "QcEntry",
    "build_qc_dossier",
    "dossier_payload",
    "dossier_seal",
    "persist_dossier",
]


@dataclass(frozen=True, slots=True)
class QcEntry:
    """One QC insertion's evidence: what was charged, what came back."""

    material_id: int
    name: str
    qc_type: QcMaterialType
    lot_number: str | None
    position: str
    crucible_status: str
    portion_g: Decimal
    gold_bead_mg: Decimal | None
    #: The reconstructed grade, or ``None`` when no grade can honestly be
    #: stated yet (not weighed; or weighed at a weight the domain refuses to
    #: grade without more information — each case carries its own flag).
    au: MeasuredValue | None
    certified_au_value_g_t: Decimal | None
    certified_au_uncertainty_g_t: Decimal | None
    z_score: Decimal | None
    advisories: tuple[Advisory, ...]


@dataclass(frozen=True, slots=True)
class QcDossier:
    batch_id: int
    batch_number: str
    batch_status: str
    entries: tuple[QcEntry, ...]
    batch_flags: tuple[Advisory, ...]


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def build_qc_dossier(session: Session, *, batch_id: int, blank_threshold_g_t: Decimal) -> QcDossier:
    """Assemble the batch's QC evidence from rows that already exist.

    Raises :class:`TransitionNotAllowedError` for a batch that has not
    finished its furnace run (**409**, same refusal family as every other
    state conflict): a dossier describes a completed run, and handing one out
    mid-fusion would invite decisions about measurements nothing has taken
    yet. An unknown batch stays a **404** (:class:`BatchNotFoundError`).
    """
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise BatchNotFoundError(f"no batch with id {batch_id}")
    if batch.status is not BatchStatus.COMPLETED:
        raise TransitionNotAllowedError(
            f"batch {batch.batch_number!r} is {batch.status.value}; a QC dossier "
            "describes a completed furnace run"
        )

    entries: list[QcEntry] = []
    crucibles = session.scalars(
        select(Crucible)
        .where(Crucible.qc_material_id.is_not(None), Crucible.batch_id == batch.id)
        .order_by(Crucible.position_row, Crucible.position_col)
    ).all()

    for crucible in crucibles:
        material = session.get(QcMaterial, crucible.qc_material_id)
        assert material is not None  # the FK guarantees it
        advisories: list[Advisory] = []
        au: MeasuredValue | None = None
        z: Decimal | None = None

        if crucible.status.value != "weighed" or crucible.gold_bead_mg is None:
            advisories.append(
                Advisory(
                    "not_yet_weighed",
                    "the crucible has no final weighing on record; there is no "
                    "measurement to report",
                )
            )
        else:
            assert crucible.gold_bead_mg is not None  # narrowing for mypy
            try:
                au = gravimetric_grade(
                    gold_bead_mg=crucible.gold_bead_mg,
                    sample_weight_g=crucible.sample_weight_g,
                    balance_sensitivity_mg=None,
                )
            except AssayCalculationError:
                # A weight the domain refuses to grade blind — most honestly a
                # zero bead, which needs a stated balance sensitivity before it
                # is either a detect or a non-detect. Flag it; never guess.
                advisories.append(
                    Advisory(
                        "grade_requires_balance_sensitivity",
                        "the bead weight cannot be graded without a stated balance "
                        "sensitivity; record one at result entry to resolve it",
                    )
                )

        if material.qc_type in (QcMaterialType.BLANK, QcMaterialType.COARSE_BLANK):
            if au is not None:
                advisory = blank_advisory(au, threshold_g_t=blank_threshold_g_t)
                if advisory is not None:
                    advisories.append(advisory)
        elif material.qc_type is QcMaterialType.CRM:
            if (
                material.certified_au_value_g_t is None
                or material.certified_au_uncertainty_g_t is None
            ):
                advisories.append(
                    Advisory(
                        "crm_missing_certified_value",
                        "the material has no certified grade on record; nothing to compare against",
                    )
                )
            elif au is not None:
                assert material.certified_au_uncertainty_g_t is not None
                assert material.certified_au_value_g_t is not None
                z, advisory = crm_z_score(
                    au,
                    certified_au_value_g_t=material.certified_au_value_g_t,
                    certified_au_uncertainty_g_t=material.certified_au_uncertainty_g_t,
                )
                if advisory is not None:
                    advisories.append(advisory)

        entries.append(
            QcEntry(
                material_id=material.id,
                name=material.name,
                qc_type=material.qc_type,
                lot_number=material.lot_number,
                position=f"{crucible.position_row}-{crucible.position_col}",
                crucible_status=crucible.status.value,
                portion_g=crucible.sample_weight_g,
                gold_bead_mg=crucible.gold_bead_mg,
                au=au,
                certified_au_value_g_t=material.certified_au_value_g_t,
                certified_au_uncertainty_g_t=material.certified_au_uncertainty_g_t,
                z_score=z,
                advisories=tuple(advisories),
            )
        )

    batch_flags: tuple[Advisory, ...] = ()
    if not entries:
        batch_flags = (
            Advisory(
                "no_qc_inserted",
                "this batch carried no QC insertions; insertion is recorded, not "
                "enforced (see PROGRESS.md)",
            ),
        )

    return QcDossier(
        batch_id=batch.id,
        batch_number=batch.batch_number,
        batch_status=batch.status.value,
        entries=tuple(entries),
        batch_flags=batch_flags,
    )


def dossier_payload(dossier: QcDossier) -> dict[str, object]:
    """The dossier as plain data — the exact structure the seal covers.

    Kept here rather than in ``web/schemas.py`` for the same reason
    provenance's payload lives in its service: the seal must be computable
    without FastAPI, so Sentinel (or any verifier) reproduces it from the
    same rows. Every Decimal renders as its string; nothing here can drift
    from the bytes a client receives because both render *this* structure.
    """
    return {
        "batch": {"id": dossier.batch_id, "batch_number": dossier.batch_number},
        "entries": [
            {
                "material": {
                    "id": e.material_id,
                    "name": e.name,
                    "qc_type": e.qc_type.value,
                    "lot_number": e.lot_number,
                },
                "position": e.position,
                "crucible_status": e.crucible_status,
                "portion_g": _decimal(e.portion_g),
                "gold_bead_mg": _decimal(e.gold_bead_mg),
                "au": None
                if e.au is None
                else {
                    "value": str(e.au.value),
                    "detection_limit": _decimal(e.au.detection_limit),
                    "censored": e.au.censored,
                    "unit": e.au.unit.value,
                },
                "certified_au_value_g_t": _decimal(e.certified_au_value_g_t),
                "certified_au_uncertainty_g_t": _decimal(e.certified_au_uncertainty_g_t),
                "z_score": _decimal(e.z_score),
                "advisories": [{"code": a.code, "detail": a.detail} for a in e.advisories],
            }
            for e in dossier.entries
        ],
        "batch_flags": [{"code": a.code, "detail": a.detail} for a in dossier.batch_flags],
    }


def dossier_seal(dossier: QcDossier) -> str:
    """``sha256`` over the canonical rendering of :func:`dossier_payload`."""
    return canonical_sha256(dossier_payload(dossier))


def persist_dossier(session: Session, dossier: QcDossier, *, actor_id: int | None) -> str:
    """Store these exact bytes and point the batch at them; return the address.

    Content-addressing makes this idempotent: unchanged evidence hashes to
    the existing address, deduplicates in the blob store, and leaves the
    pointer — and the audit trail — untouched. A genuinely new dossier gets a
    new address, a fresh blob, one audit event on the batch (``create`` the
    first time, ``amend`` after), and the previous evidence stays stored and
    reachable under its own hash.
    """
    payload = dossier_payload(dossier)
    content = canonical_json(payload).encode("utf-8")
    blob = ensure_blob(session, content=content, content_type="application/json")

    batch = session.get(Batch, dossier.batch_id)
    assert batch is not None  # build_qc_dossier already resolved it
    if batch.qc_dossier_sha256 == blob.sha256:
        return blob.sha256

    action = "amend" if batch.qc_dossier_sha256 is not None else "create"
    previous = batch.qc_dossier_sha256
    before: dict[str, object] | None = None if previous is None else {"qc_dossier_sha256": previous}
    batch.qc_dossier_sha256 = blob.sha256
    batch.qc_dossier_generated_at = datetime.now(UTC)

    record_audit_event(
        session,
        table_name="batch",
        record_id=batch.id,
        action=action,
        actor_id=actor_id,
        before=before,
        after={"qc_dossier_sha256": blob.sha256},
        # The schema requires every amendment to state its reason, and this
        # one has an honest, self-describing one: the evidence itself
        # changed. The previous address rides in `before`; naming it here too
        # keeps the reason readable without parsing JSON.
        reason=(
            None
            if previous is None
            else f"regenerated after new measurements; previous seal {previous[:12]}"
        ),
    )
    return blob.sha256
