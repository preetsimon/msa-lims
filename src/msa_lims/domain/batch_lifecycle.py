"""The furnace batch state machine, and the furnace-geometry checks around it.

A batch's states are physical, not administrative::

    Pending ──► Charging ──► InFusion ──► Fused ──► InCupellation ──► Cupelled ──► Completed

Unlike a sample, there is no branch and no way back. A sample can be returned
for re-assay because a bad result is a data problem; a batch cannot be
un-fired because it is a description of a furnace run that already happened.
A re-assay charges the sample into a *new* batch — see
:mod:`msa_lims.domain.lifecycle`'s ``ASSAYED -> READY_FOR_ASSAY`` transition
— it never rewinds this one. This module reuses
:mod:`msa_lims.domain.lifecycle`'s error types rather than defining a parallel
hierarchy: "the move is not legal" and "the actor may not make it" mean the
same thing for a batch as for a sample, and a caller that already catches
those exceptions for sample transitions catches these too.
"""

from __future__ import annotations

from dataclasses import dataclass

from msa_lims.domain.enums import BatchStatus, CrucibleStatus, Role
from msa_lims.domain.lifecycle import (
    BENCH_ROLES,
    InsufficientRoleError,
    TransitionNotAllowedError,
)


class FurnacePositionError(ValueError):
    """A crucible position is not a real slot in the furnace tray."""


@dataclass(frozen=True, slots=True)
class BatchTransition:
    source: BatchStatus
    target: BatchStatus
    allowed_roles: frozenset[Role]
    description: str = ""


#: The complete, linear set of legal moves. Every stage is bench work, same as
#: the sample lifecycle's day-to-day transitions — advancing a batch is not a
#: supervisory act the way rejecting a sample or signing a certificate is.
BATCH_TRANSITIONS: tuple[BatchTransition, ...] = (
    BatchTransition(
        BatchStatus.PENDING, BatchStatus.CHARGING, BENCH_ROLES, description="open for charging"
    ),
    BatchTransition(
        BatchStatus.CHARGING,
        BatchStatus.IN_FUSION,
        BENCH_ROLES,
        description="close charging and load the furnace",
    ),
    BatchTransition(
        BatchStatus.IN_FUSION, BatchStatus.FUSED, BENCH_ROLES, description="record fusion complete"
    ),
    BatchTransition(
        BatchStatus.FUSED,
        BatchStatus.IN_CUPELLATION,
        BENCH_ROLES,
        description="begin cupellation",
    ),
    BatchTransition(
        BatchStatus.IN_CUPELLATION,
        BatchStatus.CUPELLED,
        BENCH_ROLES,
        description="record cupellation complete",
    ),
    BatchTransition(
        BatchStatus.CUPELLED, BatchStatus.COMPLETED, BENCH_ROLES, description="close the batch"
    ),
)

#: Bulk crucible status a batch transition drives, for the two stages every
#: crucible in the tray moves through together. ``None`` for every other
#: transition: charging is a per-crucible act with its own write path, and
#: parting/weighing are per-crucible measurements this phase does not wire in
#: (see PROGRESS.md), so nothing here should silently invent a status for
#: them.
_BULK_CRUCIBLE_STATUS: dict[BatchStatus, CrucibleStatus] = {
    BatchStatus.FUSED: CrucibleStatus.FUSED,
    BatchStatus.CUPELLED: CrucibleStatus.CUPELLED,
}


def check_batch_transition(*, source: BatchStatus, target: BatchStatus, role: Role) -> None:
    """Raise unless this actor may move a batch from ``source`` to ``target``."""
    if source is target:
        raise TransitionNotAllowedError(f"the batch is already {source.value}")

    transition = next(
        (t for t in BATCH_TRANSITIONS if t.source is source and t.target is target), None
    )
    if transition is None:
        raise TransitionNotAllowedError(f"a batch cannot go from {source.value} to {target.value}")
    if role not in transition.allowed_roles:
        raise InsufficientRoleError(
            f"{role.value} may not {transition.description}; "
            f"this needs {' or '.join(sorted(r.value for r in transition.allowed_roles))}"
        )


def bulk_crucible_status(batch_target: BatchStatus) -> CrucibleStatus | None:
    """The status every charged crucible takes on when the batch reaches
    ``batch_target``, or ``None`` if this batch move does not bulk-update
    crucibles."""
    return _BULK_CRUCIBLE_STATUS.get(batch_target)


def check_position(*, row: int, col: int, rows: int, cols: int) -> None:
    """Raise unless ``(row, col)`` is a real slot in a ``rows`` x ``cols`` tray.

    Positions are 1-indexed, matching how a technician reads a tray — "row 3,
    column 4" — rather than a 0-indexed array offset nobody says out loud.
    """
    if not (1 <= row <= rows):
        raise FurnacePositionError(f"row {row} is outside the furnace tray (1-{rows})")
    if not (1 <= col <= cols):
        raise FurnacePositionError(f"column {col} is outside the furnace tray (1-{cols})")


__all__ = [
    "BATCH_TRANSITIONS",
    "BatchTransition",
    "FurnacePositionError",
    "bulk_crucible_status",
    "check_batch_transition",
    "check_position",
]
