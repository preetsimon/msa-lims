"""Sequential document numbers that survive two writers racing for one.

``SUB-2026-0001``-shaped numbers are allocated as "count the existing rows,
add one" — correct only under a single writer. Two concurrent front-desk
requests can both compute the same number; the database's UNIQUE index stops
the second one, and without what follows that would surface as an unhandled
IntegrityError and a 500.

The remedy here is a retry, not a redesign, for three reasons:

* No schema change. A per-year sequence is the alternative and needs a
  migration plus per-year bookkeeping for a collision that has never yet
  happened.
* The retry lives in a SAVEPOINT (:meth:`Session.begin_nested`), so a failed
  attempt rolls back only the row insert — the caller's outer transaction,
  already holding validation work, is untouched.
* Only a genuine unique violation retries (SQLSTATE ``23505``). Any other
  integrity failure — a CHECK, a foreign key — propagates immediately, because
  retrying a bug is how bugs get hidden.

Post-insert UPDATE was never an option for the append-only tables:
``certificate`` holds no UPDATE grant by design, so the number must be right
before the INSERT, which is exactly what this helper guarantees or refuses.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

#: Postgres SQLSTATE for a unique-constraint violation. Checked explicitly so
#: a retry never swallows an unrelated IntegrityError.
UNIQUE_VIOLATION_SQLSTATE = "23505"

#: Bounded so a pathological storm cannot spin forever. Each attempt recomputes
#: from the live count, so one extra attempt absorbs any realistic race; five
#: absorbs five simultaneous writers landing on the same slot.
_MAX_ATTEMPTS = 5

T = TypeVar("T")


def count_with_prefix(session: Session, model: type, attribute: str, prefix: str) -> int:
    """Rows of ``model`` whose number column starts with ``prefix``."""
    return (
        session.scalar(
            select(func.count())
            .select_from(model)
            .where(getattr(model, attribute).like(f"{prefix}%"))
        )
        or 0
    )


def _is_unique_violation(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "pgcode", None) == UNIQUE_VIOLATION_SQLSTATE


def insert_with_unique_number(
    session: Session,
    build_row: Callable[[str], T],
    allocate_number: Callable[[int], str],
) -> T:
    """Insert a row whose business number must be unique, racing safely.

    ``allocate_number(attempt)`` returns the candidate for this attempt — the
    attempt counter lets the allocator step past numbers a concurrent writer
    may have taken between the COUNT and the INSERT. ``build_row(number)``
    constructs the ORM instance fresh each attempt: after a savepoint rollback,
    the previous instance's identity, id, and inserted state are stale.

    Returns the persisted row with its id populated. Re-raises any
    IntegrityError that was not a unique violation, and raises RuntimeError if
    the attempt bound is exhausted — which means more than ``_MAX_ATTEMPTS``
    concurrent writers picked the same slot. Loud, not silent.
    """
    last_exc: IntegrityError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        row = build_row(allocate_number(attempt))
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
            return row
        except IntegrityError as exc:
            if not _is_unique_violation(exc):
                raise
            last_exc = exc
    assert last_exc is not None
    raise RuntimeError(
        f"could not allocate a unique number after {_MAX_ATTEMPTS} attempts"
    ) from last_exc
