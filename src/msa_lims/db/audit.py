"""The single write path for ``audit_event`` rows, and the chain's own
verifier.

Every service module used to construct ``AuditEvent(...)`` itself — nine
files, thirteen call sites, most with their own small hand-rolled ``_audit``
helper. That was fine while an audit row was just a fact to record; it stops
being fine the moment recording a fact also means extending a hash chain
(:mod:`msa_lims.domain.audit_chain`, audit idea #1), because a chain with
even one call site that forgot to link into it is not a chain — it is a
chain with an undetectable gap. Centralising the write here means the link
cannot be forgotten by a *future* service either: every module already
depends on this one to write an audit row at all.

The chain's ordering assumption matches ``db/numbering.py``'s own, stated
plainly rather than solved here: this reads the current tip and computes the
next link within the caller's existing transaction, correct under a single
writer or requests already serialized by other means, and not defended
against two genuinely concurrent writers racing for the same tip the way
``insert_with_unique_number`` defends sequential numbering. Real remaining
scope if that ever matters in production; see PROGRESS.md.

:func:`verify_chain` is the read side: walk the table, recompute every hash
independently, and stop at the first row that disagrees with what is
stored. ``python -m msa_lims.db.verify_chain`` runs it as a standalone
command (mirrors ``db/seed.py``'s own script/library split);
``GET /api/audit/verify`` runs the identical function over HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent
from msa_lims.domain.audit_chain import compute_entry_hash


def record_audit_event(
    session: Session,
    *,
    table_name: str,
    record_id: int,
    action: str,
    actor_id: int | None,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    reason: str | None = None,
    actor_ip: str | None = None,
) -> AuditEvent:
    """Write one audit row, linked to the current chain tip.

    Returns the (added, flushed by the caller's own later flush) event —
    every existing call site ignored the return value of the plain
    ``AuditEvent`` constructor it used to call, and can keep doing so; a few
    tests want it back to assert on the row it just wrote.
    """
    prev_entry_hash = session.scalar(
        select(AuditEvent.entry_hash).order_by(AuditEvent.id.desc()).limit(1)
    )
    entry_hash = compute_entry_hash(
        prev_entry_hash=prev_entry_hash,
        table_name=table_name,
        record_id=record_id,
        action=action,
        before=before,
        after=after,
        reason=reason,
        actor_id=actor_id,
        actor_ip=actor_ip,
    )
    event = AuditEvent(
        table_name=table_name,
        record_id=record_id,
        action=action,
        before=before,
        after=after,
        reason=reason,
        actor_id=actor_id,
        actor_ip=actor_ip,
        prev_entry_hash=prev_entry_hash,
        entry_hash=entry_hash,
    )
    session.add(event)
    return event


@dataclass(frozen=True, slots=True)
class ChainBreak:
    """Where the stored chain stopped matching what verification
    independently recomputes, and which of the two checks failed."""

    id: int
    reason: str


@dataclass(frozen=True, slots=True)
class ChainVerification:
    verified_count: int
    valid: bool
    head_hash: str | None
    first_break: ChainBreak | None


def verify_chain(session: Session, *, upto: int | None = None) -> ChainVerification:
    """Walk ``audit_event`` in ``id`` order, recomputing each row's
    ``entry_hash`` independently and comparing it against what is stored —
    "recompute, don't trust", the identical posture
    ``certificates/service.py``'s own hash check already takes on a PDF,
    applied here to the whole audit history rather than one document.

    Stops at the first row that does not verify (either its stored
    ``prev_entry_hash`` does not match the previous row's own ``entry_hash``,
    or its own ``entry_hash`` does not match recomputation) rather than
    scanning past it — a broken link invalidates everything chained after
    it, so reporting further "problems" downstream of a genuine break would
    be noise, not information.
    """
    # populate_existing=True: without it, a row already in this Session's
    # identity map (because this same session wrote it earlier) comes back
    # from the query unchanged from memory, not re-read from the database —
    # the exact case where a caller most needs the real, current row, since
    # a session that never sees anyone else's writes would report "valid"
    # over data someone tampered with through a different connection.
    stmt = select(AuditEvent).order_by(AuditEvent.id).execution_options(populate_existing=True)
    if upto is not None:
        stmt = stmt.where(AuditEvent.id <= upto)

    prev_hash: str | None = None
    verified = 0
    for row in session.scalars(stmt):
        if row.prev_entry_hash != prev_hash:
            return ChainVerification(
                verified_count=verified,
                valid=False,
                head_hash=prev_hash,
                first_break=ChainBreak(
                    id=row.id,
                    reason="prev_entry_hash does not match the previous row's entry_hash",
                ),
            )
        expected = compute_entry_hash(
            prev_entry_hash=row.prev_entry_hash,
            table_name=row.table_name,
            record_id=row.record_id,
            action=row.action,
            before=row.before,
            after=row.after,
            reason=row.reason,
            actor_id=row.actor_id,
            actor_ip=row.actor_ip,
        )
        if expected != row.entry_hash:
            return ChainVerification(
                verified_count=verified,
                valid=False,
                head_hash=prev_hash,
                first_break=ChainBreak(id=row.id, reason="entry_hash does not match recomputation"),
            )
        prev_hash = row.entry_hash
        verified += 1

    return ChainVerification(
        verified_count=verified, valid=True, head_hash=prev_hash, first_break=None
    )
