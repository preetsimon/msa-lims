"""Furnace batching: opening a batch, charging crucibles, and firing it through.

**A batch's crucibles are charged one at a time, and only while the batch is
``CHARGING``.** ``PENDING -> CHARGING`` ("open for charging") is a deliberate,
separate step — mirroring the sample lifecycle's "start preparation" — rather
than something the first charge does implicitly, so a batch's tray is always
either open for placement or not, never ambiguously both.

**Charging a sample into a crucible genuinely calls
``domain.lifecycle.check_transition`` for ``READY_FOR_ASSAY -> IN_ASSAY``** —
the honest transition, not a bypass. Earlier phases could not do this
honestly: nothing existed yet to move a sample to ``READY_FOR_ASSAY``, so
charging accepted any pre-assay status with a hand-rolled guard and said so in
this docstring. As of Phase 3's ``sample_lifecycle/service.py``, a sample
reaches ``READY_FOR_ASSAY`` for real — through prep or the pulp shortcut —
and charging now requires it, the same way certificate issuance requires
``ASSAYED`` for the real ``ASSAYED -> REPORTED`` move. A sample still in
``RECEIVED`` or ``IN_PREP`` is refused with the same
``TransitionNotAllowedError`` (**409**) a skipped furnace stage gets, naming
what state it needs to reach first.

**A batch's own status is a separate, linear machine** (see
:mod:`msa_lims.domain.batch_lifecycle`) with no branch and no way back — a
furnace run is a physical event, not a document to correct. ``FUSED`` and
``CUPELLED`` bulk-advance every charged crucible's status in lockstep, because
a furnace fuses or cupels a whole tray at once.

**Parting and weighing are per-crucible acts**, recorded one crucible at a
time once cupellation has released them from the tray: parting stores the
lead button, prill, and acid volume (``CUPELLED -> PARTED``); the final
weighing stores the gold bead (``PARTED -> WEIGHED``). Each move carries the
measurements that witness it — a status advance with nothing behind it would
be a claim about the world nobody made — and each is stored once, at the
moment of the physical act, never overwritten: a result naming this crucible
reads them back rather than being retyped numbers that could disagree.

**A crucible holds a sample or a QC material — never both, and not neither**
(the database carries the same rule as a CHECK constraint). The charge request
names one or the other, exactly like a fire assay result names either a
crucible or raw weighings. A QC insertion — a CRM, a blank from stock — is
charged like any other crucible: same bench role, same batch-must-be-
``CHARGING`` gate, same position rules, same flux scaling against its weighed-
out charge. What differs is everything downstream of the furnace: no sample
lifecycle moves, because no sample is involved; the bead lands on the crucible
row through the same parting/weighing path; and nothing here judges it —
grading and warning limits are QC Sentinel's job on export (Phase 5), not this
system's. Insertion is *recorded*, not *enforced*: a batch may still fire
without one. Duplicate-type QC (re-inserting an existing sample) is not
modelled yet, so any insertion-counting rule written today would guard half
the picture; recording honestly is the mechanical prerequisite any future
enforcement policy would need, and inventing the policy itself is not this
schema's call to make.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import Batch, Crucible, FluxRecipe, LabUser, QcMaterial, Sample
from msa_lims.domain.batch_lifecycle import (
    bulk_crucible_status,
    check_batch_transition,
    check_crucible_transition,
    check_position,
)
from msa_lims.domain.enums import (
    BatchStatus,
    CrucibleStatus,
    DuplicateInsertionType,
    Role,
    SampleStatus,
)
from msa_lims.domain.flux import FluxAmounts, scale_flux_charge
from msa_lims.domain.lifecycle import (
    BENCH_ROLES,
    InsufficientRoleError,
    TransitionNotAllowedError,
    check_transition,
)
from msa_lims.fire_assay_results.service import CrucibleNotFoundError, SampleNotFoundError
from msa_lims.flux_recipes.service import FluxRecipeNotFoundError
from msa_lims.qc_materials.service import QcMaterialNotFoundError


class BatchNotFoundError(ValueError):
    """No batch with this id exists."""


class BatchValidationError(ValueError):
    """One or more problems with a batch operation, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


class CrucibleValidationError(ValueError):
    """One or more problems with charging a crucible, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class BatchInput:
    opened_at: datetime
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class CrucibleChargeInput:
    batch_id: int
    #: The sample this crucible assays — or leave unset and name
    #: ``qc_material_id`` for a QC insertion. Exactly one of the two; see the
    #: module docstring.
    sample_id: int | None
    qc_material_id: int | None
    flux_recipe_id: int
    position_row: int
    position_col: int
    sample_weight_g: Decimal
    charged_at: datetime
    notes: str | None = None
    #: Set only alongside ``sample_id``: this crucicle re-inserts a sample
    #: already charged elsewhere in the tray (a field/prep/pulp duplicate).
    insertion_type: DuplicateInsertionType | None = None


@dataclass(frozen=True, slots=True)
class CruciblePartingInput:
    """The measurements taken while parting one cupelled crucible."""

    lead_button_weight_mg: Decimal
    prill_weight_mg: Decimal
    parting_acid_volume_ml: Decimal
    parted_at: datetime


@dataclass(frozen=True, slots=True)
class CrucibleWeighingInput:
    """The final gold-bead weighing for one parted crucible."""

    gold_bead_mg: Decimal
    weighed_at: datetime


@dataclass(frozen=True, slots=True)
class CrucibleSlot:
    """One crucible plus the human-readable label of whatever it holds — a
    sample's own label, or a QC material's name and type — assembled once
    here in a single query rather than requiring a caller rendering a tray
    of up to 36 of these to look each one up separately."""

    crucible: Crucible
    sample_label: str | None
    qc_material_name: str | None
    qc_material_type: str | None


@dataclass(frozen=True, slots=True)
class BatchDetail:
    batch: Batch
    crucibles: tuple[CrucibleSlot, ...]


class BatchService:
    def __init__(self, session: Session, *, furnace_rows: int, furnace_columns: int) -> None:
        self._session = session
        self._furnace_rows = furnace_rows
        self._furnace_columns = furnace_columns

    def create_batch(self, data: BatchInput, *, opened_by: LabUser, actor_role: Role) -> Batch:
        if actor_role not in BENCH_ROLES:
            raise InsufficientRoleError(
                f"{actor_role.value} may not open a batch; this needs one of "
                + ", ".join(sorted(role.value for role in BENCH_ROLES))
            )

        batch = Batch(
            batch_number=self._allocate_number(data.opened_at),
            status=BatchStatus.PENDING,
            opened_by_id=opened_by.id,
            opened_at=data.opened_at,
            notes=data.notes,
        )
        self._session.add(batch)
        self._session.flush()

        record_audit_event(
            self._session,
            table_name="batch",
            record_id=batch.id,
            action="create",
            actor_id=opened_by.id,
            after={"batch_number": batch.batch_number, "status": batch.status.value},
        )
        return batch

    def charge_crucible(
        self, data: CrucibleChargeInput, *, charged_by: LabUser, actor_role: Role
    ) -> Crucible:
        if actor_role not in BENCH_ROLES:
            raise InsufficientRoleError(
                f"{actor_role.value} may not charge a crucible; this needs one of "
                + ", ".join(sorted(role.value for role in BENCH_ROLES))
            )

        # The request names what goes into the slot — a sample or a QC
        # material, one or the other, never both and not neither. Like the
        # position and flux errors below, this is a request that cannot be
        # carried out at all, so it refuses directly rather than collecting.
        if (data.sample_id is None) == (data.qc_material_id is None):
            raise CrucibleValidationError(
                ["name a sample or a QC material — exactly one of the two"]
            )
        if data.insertion_type is not None and data.qc_material_id is not None:
            raise CrucibleValidationError(
                ["a duplicate insertion re-inserts a sample; name sample_id, not a QC material"]
            )

        batch = self._session.get(Batch, data.batch_id)
        if batch is None:
            raise BatchNotFoundError(f"no batch with id {data.batch_id}")

        sample: Sample | None = None
        material: QcMaterial | None = None
        if data.sample_id is not None:
            sample = self._session.get(Sample, data.sample_id)
            if sample is None:
                raise SampleNotFoundError(f"no sample with id {data.sample_id}")
            if data.insertion_type is None:
                # The real transition, not a bypass — see the module docstring.
                # Raises TransitionNotAllowedError (409) for a sample not yet
                # READY_FOR_ASSAY; InsufficientRoleError cannot actually trigger
                # here since this transition's allowed_roles is BENCH_ROLES,
                # already checked above, but is not caught so a future change to
                # either role set fails loudly instead of silently.
                check_transition(
                    source=sample.status,
                    target=SampleStatus.IN_ASSAY,
                    sample_type=sample.sample_type,
                    role=actor_role,
                )
            elif sample.status is not SampleStatus.IN_ASSAY:
                # A duplicate rides along with its original: the sample must
                # genuinely be mid-assay, which only its primary charge can
                # have caused. Nothing here may move the lifecycle — the
                # original's charge owns that.
                raise TransitionNotAllowedError(
                    f"sample {sample.sample_id!r} is {sample.status.value}; a "
                    f"{data.insertion_type.value} rides along with a sample already "
                    "in assay — charge its original crucible first"
                )
        else:
            assert data.qc_material_id is not None
            material = self._session.get(QcMaterial, data.qc_material_id)
            if material is None:
                raise QcMaterialNotFoundError(f"no QC material with id {data.qc_material_id}")

        recipe = self._session.get(FluxRecipe, data.flux_recipe_id)
        if recipe is None:
            raise FluxRecipeNotFoundError(f"no flux recipe with id {data.flux_recipe_id}")

        # Physically-possible-position and calculation errors are raised
        # directly rather than collected: like domain.assay's calculations,
        # they mean the request itself cannot be carried out, not that this
        # particular attempt lost a race to another one.
        check_position(
            row=data.position_row,
            col=data.position_col,
            rows=self._furnace_rows,
            cols=self._furnace_columns,
        )
        scaled = scale_flux_charge(
            FluxAmounts(
                litharge_g=recipe.litharge_g,
                soda_ash_g=recipe.soda_ash_g,
                borax_g=recipe.borax_g,
                silica_g=recipe.silica_g,
                flour_g=recipe.flour_g,
                nitre_g=recipe.nitre_g,
            ),
            nominal_portion_g=recipe.nominal_portion_g,
            sample_weight_g=data.sample_weight_g,
        )

        problems = self._check_batch_and_position(batch, data.position_row, data.position_col)
        if material is not None and not material.is_active:
            problems.append(
                f"QC material {material.name!r} is retired and cannot be charged; "
                "register its replacement as a new material"
            )
        if problems:
            raise CrucibleValidationError(problems)

        assert sample is not None or material is not None
        crucible = Crucible(
            batch_id=batch.id,
            sample_id=sample.id if sample is not None else None,
            qc_material_id=material.id if material is not None else None,
            insertion_type=data.insertion_type,
            flux_recipe_id=recipe.id,
            position_row=data.position_row,
            position_col=data.position_col,
            status=CrucibleStatus.CHARGED,
            sample_weight_g=data.sample_weight_g,
            litharge_g=scaled.litharge_g,
            soda_ash_g=scaled.soda_ash_g,
            borax_g=scaled.borax_g,
            silica_g=scaled.silica_g,
            flour_g=scaled.flour_g,
            nitre_g=scaled.nitre_g,
            charged_by_id=charged_by.id,
            charged_at=data.charged_at,
            notes=data.notes,
        )
        self._session.add(crucible)
        self._session.flush()

        after: dict[str, object] = {
            "batch_id": batch.id,
            "position": f"{data.position_row}-{data.position_col}",
        }
        if sample is not None:
            after["sample_id"] = sample.id
            if data.insertion_type is not None:
                after["insertion_type"] = data.insertion_type.value
        else:
            assert material is not None
            after["qc_material_id"] = material.id
        record_audit_event(
            self._session,
            table_name="crucible",
            record_id=crucible.id,
            action="create",
            actor_id=charged_by.id,
            after=after,
        )
        if sample is not None and data.insertion_type is None:
            # Only a primary charge moves the lifecycle; a duplicate rides
            # along with the IN_ASSAY status its original already caused.
            sample.status = SampleStatus.IN_ASSAY
        return crucible

    def _check_batch_and_position(self, batch: Batch, row: int, col: int) -> list[str]:
        """Refusals shared by both kinds of charge: the batch must be open
        for charging, and the slot must be free."""
        problems: list[str] = []

        if batch.status is not BatchStatus.CHARGING:
            problems.append(
                f"batch {batch.batch_number!r} is {batch.status.value}, not charging; "
                "open it for charging before assigning crucibles"
            )

        occupied = self._session.scalar(
            select(Crucible).where(
                Crucible.batch_id == batch.id,
                Crucible.position_row == row,
                Crucible.position_col == col,
            )
        )
        if occupied is not None:
            problems.append(
                f"position {row}-{col} in batch {batch.batch_number!r} is already occupied"
            )
        return problems

    def record_parting(
        self,
        batch_id: int,
        crucible_id: int,
        data: CruciblePartingInput,
        *,
        parted_by: LabUser,
        actor_role: Role,
    ) -> Crucible:
        """Record one cupelled crucible's parting: lead button, prill, acid.

        Parting is bench work — the same tier as charging — and happens one
        crucible at a time after the batch has released the tray from
        cupellation. Raises ``TransitionNotAllowedError`` if the crucible has
        not been cupelled (or is already parted); both refusals are mapped
        globally like every other lifecycle refusal.
        """
        if actor_role not in BENCH_ROLES:
            raise InsufficientRoleError(
                f"{actor_role.value} may not record a crucible's parting; this needs one of "
                + ", ".join(sorted(role.value for role in BENCH_ROLES))
            )

        crucible = self._get_batch_crucible(batch_id, crucible_id)
        before_status = crucible.status
        check_crucible_transition(source=before_status, target=CrucibleStatus.PARTED)

        crucible.status = CrucibleStatus.PARTED
        crucible.lead_button_weight_mg = data.lead_button_weight_mg
        crucible.prill_weight_mg = data.prill_weight_mg
        crucible.parting_acid_volume_ml = data.parting_acid_volume_ml
        crucible.parted_at = data.parted_at

        record_audit_event(
            self._session,
            table_name="crucible",
            record_id=crucible.id,
            action="transition",
            actor_id=parted_by.id,
            before={"status": before_status.value},
            after={
                "status": CrucibleStatus.PARTED.value,
                "lead_button_weight_mg": str(data.lead_button_weight_mg),
                "prill_weight_mg": str(data.prill_weight_mg),
                "parting_acid_volume_ml": str(data.parting_acid_volume_ml),
            },
        )
        return crucible

    def record_weighing(
        self,
        batch_id: int,
        crucible_id: int,
        data: CrucibleWeighingInput,
        *,
        weighed_by: LabUser,
        actor_role: Role,
    ) -> Crucible:
        """Record one parted crucible's final gold-bead weighing.

        This is the measurement a fire assay result later reads back when it
        names the crucible (see ``fire_assay_results/service.py``) — stored
        here once, at the balance, so the result never re-types a number that
        could disagree with what was actually weighed.
        """
        if actor_role not in BENCH_ROLES:
            raise InsufficientRoleError(
                f"{actor_role.value} may not record a crucible's weighing; this needs one of "
                + ", ".join(sorted(role.value for role in BENCH_ROLES))
            )

        crucible = self._get_batch_crucible(batch_id, crucible_id)
        before_status = crucible.status
        check_crucible_transition(source=before_status, target=CrucibleStatus.WEIGHED)

        crucible.status = CrucibleStatus.WEIGHED
        crucible.gold_bead_mg = data.gold_bead_mg
        crucible.weighed_at = data.weighed_at

        record_audit_event(
            self._session,
            table_name="crucible",
            record_id=crucible.id,
            action="transition",
            actor_id=weighed_by.id,
            before={"status": before_status.value},
            after={
                "status": CrucibleStatus.WEIGHED.value,
                "gold_bead_mg": str(data.gold_bead_mg),
            },
        )
        return crucible

    def _get_batch_crucible(self, batch_id: int, crucible_id: int) -> Crucible:
        """The crucible named by both path segments, or the honest refusal.

        An unknown batch and a crucible that lives in some *other* batch are
        the same situation from this URL — the resource asked for does not
        exist here.
        """
        if self._session.get(Batch, batch_id) is None:
            raise BatchNotFoundError(f"no batch with id {batch_id}")
        crucible = self._session.get(Crucible, crucible_id)
        if crucible is None or crucible.batch_id != batch_id:
            raise CrucibleNotFoundError(f"no crucible with id {crucible_id} in batch {batch_id}")
        return crucible

    def advance_status(
        self, batch_id: int, *, target: BatchStatus, advanced_by: LabUser, actor_role: Role
    ) -> Batch:
        batch = self._session.get(Batch, batch_id)
        if batch is None:
            raise BatchNotFoundError(f"no batch with id {batch_id}")

        # Raises TransitionNotAllowedError / InsufficientRoleError; both
        # propagate and are mapped globally in web/app.py, same as every
        # other lifecycle refusal in this codebase.
        check_batch_transition(source=batch.status, target=target, role=actor_role)

        if target is BatchStatus.IN_FUSION:
            charged = self._session.scalar(
                select(func.count()).select_from(Crucible).where(Crucible.batch_id == batch.id)
            )
            if not charged:
                raise BatchValidationError(
                    [f"batch {batch.batch_number!r} has no charged crucibles to fire"]
                )

        before_status = batch.status
        batch.status = target

        new_crucible_status = bulk_crucible_status(target)
        if new_crucible_status is not None:
            for crucible in batch.crucibles:
                crucible.status = new_crucible_status

        record_audit_event(
            self._session,
            table_name="batch",
            record_id=batch.id,
            action="transition",
            actor_id=advanced_by.id,
            before={"status": before_status.value},
            after={"status": target.value},
        )
        return batch

    def _allocate_number(self, opened_at: datetime) -> str:
        """A batch number in the shape ``BATCH-2026-0001``.

        Same provisional-numbering caveat as ``submission_number`` and
        ``certificate_number`` — count-based allocation assumes a single
        writer — see ``submissions/service.py``.
        """
        prefix = f"BATCH-{opened_at.year}-"
        count = (
            self._session.scalar(
                select(func.count()).select_from(Batch).where(Batch.batch_number.like(f"{prefix}%"))
            )
            or 0
        )
        return f"{prefix}{count + 1:04d}"


def get_batch_detail(session: Session, batch_id: int) -> BatchDetail:
    """A batch and its crucibles, ordered the way a technician reads a tray —
    row-major, top-left first — each carrying whatever it holds' label.

    Two ``LEFT JOIN``s, not a relationship traversal: a crucible's
    ``sample_id``/``qc_material_id`` are mutually exclusive (see the model's
    own CHECK constraint), so exactly one of the two joined rows is non-null
    per crucible, and one query gets every label a tray needs to render
    without a lookup per slot.
    """
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise BatchNotFoundError(f"no batch with id {batch_id}")

    rows = session.execute(
        select(Crucible, Sample.sample_id, QcMaterial.name, QcMaterial.qc_type)
        .outerjoin(Sample, Crucible.sample_id == Sample.id)
        .outerjoin(QcMaterial, Crucible.qc_material_id == QcMaterial.id)
        .where(Crucible.batch_id == batch_id)
        .order_by(Crucible.position_row, Crucible.position_col)
    ).all()

    crucibles = tuple(
        CrucibleSlot(
            crucible=crucible,
            sample_label=sample_label,
            qc_material_name=qc_material_name,
            qc_material_type=qc_type.value if qc_type is not None else None,
        )
        for crucible, sample_label, qc_material_name, qc_type in rows
    )
    return BatchDetail(batch=batch, crucibles=crucibles)


def list_batches(session: Session, *, limit: int = 100) -> list[Batch]:
    """The most recent batches, newest first — the same lean, one-query
    shape ``samples/service.py``'s ``list_samples`` already established for
    a listing endpoint: no per-batch crucible count or tray preview, since
    nothing here needs one yet and it would cost a query per row to get."""
    return list(session.scalars(select(Batch).order_by(Batch.id.desc()).limit(limit)))
