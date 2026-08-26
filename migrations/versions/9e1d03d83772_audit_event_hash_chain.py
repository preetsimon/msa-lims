"""audit event hash chain

Revision ID: 9e1d03d83772
Revises: b7c2f4a91d08
Create Date: 2026-08-25 22:24:26.339312
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "9e1d03d83772"
down_revision: str | None = "b7c2f4a91d08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A frozen copy of msa_lims.domain.audit_chain's hashing, not an import of
# it. A migration must produce the identical backfill result forever, even
# after that module's own logic changes for a future need — the standard
# reason Alembic migrations never depend on application code.
_GENESIS_PREV_HASH = "0" * 64


def _compute_entry_hash(
    prev_entry_hash: str | None,
    table_name: str,
    record_id: int,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    reason: str | None,
    actor_id: int | None,
    actor_ip: str | None,
) -> str:
    prev = prev_entry_hash if prev_entry_hash is not None else _GENESIS_PREV_HASH
    payload = {
        "prev_entry_hash": prev,
        "table_name": table_name,
        "record_id": record_id,
        "action": action,
        "before": before,
        "after": after,
        "reason": reason,
        "actor_id": actor_id,
        "actor_ip": actor_ip,
    }
    entry = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256((prev + entry).encode("utf-8")).hexdigest()


def _backfill_chain() -> None:
    """Any row already in ``audit_event`` (a fresh deployment has none;
    anything seeded or demoed locally does) needs a hash too, walked in
    ``id`` order — the same "single writer, sequential ids" assumption the
    hash chain itself relies on at write time."""
    bind = op.get_bind()
    audit_event = sa.table(
        "audit_event",
        sa.column("id", sa.BigInteger),
        sa.column("table_name", sa.String),
        sa.column("record_id", sa.BigInteger),
        sa.column("action", sa.String),
        sa.column("before", JSONB),
        sa.column("after", JSONB),
        sa.column("reason", sa.Text),
        sa.column("actor_id", sa.BigInteger),
        sa.column("actor_ip", sa.String),
        sa.column("prev_entry_hash", sa.String),
        sa.column("entry_hash", sa.String),
    )

    rows = bind.execute(
        sa.select(
            audit_event.c.id,
            audit_event.c.table_name,
            audit_event.c.record_id,
            audit_event.c.action,
            audit_event.c.before,
            audit_event.c.after,
            audit_event.c.reason,
            audit_event.c.actor_id,
            audit_event.c.actor_ip,
        ).order_by(audit_event.c.id)
    ).fetchall()

    prev_hash: str | None = None
    for row in rows:
        entry_hash = _compute_entry_hash(
            prev_hash,
            row.table_name,
            row.record_id,
            row.action,
            row.before,
            row.after,
            row.reason,
            row.actor_id,
            row.actor_ip,
        )
        bind.execute(
            audit_event.update()
            .where(audit_event.c.id == row.id)
            .values(prev_entry_hash=prev_hash, entry_hash=entry_hash)
        )
        prev_hash = entry_hash


def upgrade() -> None:
    # entry_hash lands nullable first so existing rows (a fresh deployment
    # has none; anything seeded or demoed locally does) don't violate NOT
    # NULL before the backfill below has run.
    op.add_column("audit_event", sa.Column("prev_entry_hash", sa.String(length=64), nullable=True))
    op.add_column("audit_event", sa.Column("entry_hash", sa.String(length=64), nullable=True))

    _backfill_chain()

    op.alter_column("audit_event", "entry_hash", nullable=False)


def downgrade() -> None:
    op.drop_column("audit_event", "entry_hash")
    op.drop_column("audit_event", "prev_entry_hash")
