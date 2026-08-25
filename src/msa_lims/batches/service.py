"""Furnace batching: opening a batch, charging crucibles, and firing it through.

**A batch's crucibles are charged one at a time, and only while the batch is
``CHARGING``.** ``PENDING -> CHARGING`` ("open for charging") is a deliberate,
separate step — mirroring the sample lifecycle's "start preparation" — rather
than something the first charge does implicitly, so a batch's tray is always
either open for placement or not, never ambiguously both.

**Charging bypasses ``domain.lifecycle``'s ``READY_FOR_ASSAY -> IN_ASSAY``
transition, the same way ``fire_assay_results/service.py`` bypasses the
lifecycle table for entering a result.** Prep-stage tracking does not exist
yet (see that module's own docstring for the identical reasoning), so no
sample can honestly reach ``READY_FOR_ASSAY`` through the modelled path. A
sample is chargeable from any pre-assay status — everything except
``IN_ASSAY``, ``ASSAYED``, ``REPORTED`` and ``REJECTED`` — and charging moves
it straight to ``IN_ASSAY``. This is the honest reflection of what this
system currently tracks, not a shortcut hidden behind the full lifecycle
machinery.

**A batch's own status is a separate, linear machine** (see
:mod:`msa_lims.domain.batch_lifecycle`) with no branch and no way back — a
furnace run is a physical event, not a document to correct. ``FUSED`` and
``CUPELLED`` bulk-advance every charged crucible's status in lockstep, because
a furnace fuses or cupels a whole tray at once; parting and weighing remain
per-crucible measurements this phase does not wire in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, Batch, Crucible, FluxRecipe, LabUser, Sample
from msa_lims.domain.batch_lifecycle import (
    bulk_crucible_status,
    check_batch_transition,
    check_position,
)
from msa_lims.domain.enums import BatchStatus, CrucibleStatus, Role, SampleStatus
from msa_lims.domain.flux import FluxAmounts, scale_flux_charge
from msa_lims.domain.lifecycle import BENCH_ROLES, InsufficientRoleError
from msa_lims.fire_assay_results.service import SampleNotFoundError
from msa_lims.flux_recipes.service import FluxRecipeNotFoundError

#: Sample statuses a crucible cannot be charged from — the terminal statuses
#: and IN_ASSAY itself, which means the sample is already in another batch.
_NOT_CHARGEABLE: frozenset[SampleStatus] = frozenset(
    {SampleStatus.IN_ASSAY, SampleStatus.ASSAYED, SampleStatus.REPORTED, SampleStatus.REJECTED}
)


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
    sample_id: int
    flux_recipe_id: int
    position_row: int
    position_col: int
    sample_weight_g: Decimal
    charged_at: datetime
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class BatchDetail:
    batch: Batch
    crucibles: tuple[Crucible, ...]


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

        self._session.add(
            AuditEvent(
                table_name="batch",
                record_id=batch.id,
                action="create",
                after={"batch_number": batch.batch_number, "status": batch.status.value},
                actor_id=opened_by.id,
            )
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

        batch = self._session.get(Batch, data.batch_id)
        if batch is None:
            raise BatchNotFoundError(f"no batch with id {data.batch_id}")

        sample = self._session.get(Sample, data.sample_id)
        if sample is None:
            raise SampleNotFoundError(f"no sample with id {data.sample_id}")

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

        problems = self._check_charge(batch, sample, data)
        if problems:
            raise CrucibleValidationError(problems)

        crucible = Crucible(
            batch_id=batch.id,
            sample_id=sample.id,
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

        self._session.add(
            AuditEvent(
                table_name="crucible",
                record_id=crucible.id,
                action="create",
                after={
                    "batch_id": batch.id,
                    "sample_id": sample.id,
                    "position": f"{data.position_row}-{data.position_col}",
                },
                actor_id=charged_by.id,
            )
        )
        sample.status = SampleStatus.IN_ASSAY
        return crucible

    def _check_charge(self, batch: Batch, sample: Sample, data: CrucibleChargeInput) -> list[str]:
        problems: list[str] = []

        if batch.status is not BatchStatus.CHARGING:
            problems.append(
                f"batch {batch.batch_number!r} is {batch.status.value}, not charging; "
                "open it for charging before assigning crucibles"
            )
        if sample.status in _NOT_CHARGEABLE:
            problems.append(
                f"sample {sample.sample_id!r} is {sample.status.value} and cannot be charged"
            )

        occupied = self._session.scalar(
            select(Crucible).where(
                Crucible.batch_id == batch.id,
                Crucible.position_row == data.position_row,
                Crucible.position_col == data.position_col,
            )
        )
        if occupied is not None:
            problems.append(
                f"position {data.position_row}-{data.position_col} in batch "
                f"{batch.batch_number!r} is already occupied"
            )
        return problems

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

        self._session.add(
            AuditEvent(
                table_name="batch",
                record_id=batch.id,
                action="transition",
                before={"status": before_status.value},
                after={"status": target.value},
                actor_id=advanced_by.id,
            )
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
    row-major, top-left first."""
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise BatchNotFoundError(f"no batch with id {batch_id}")

    crucibles = tuple(
        sorted(batch.crucibles, key=lambda crucible: (crucible.position_row, crucible.position_col))
    )
    return BatchDetail(batch=batch, crucibles=crucibles)
