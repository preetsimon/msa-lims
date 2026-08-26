"""append-only grants for stored_blob

Revision ID: 6f4c9a2be7d1
Revises: d38f8b50f074
Create Date: 2026-08-25

The next table to join ``b1d0c4e77a10``'s append-only tier. A stored blob is
addressed by its own sha256: its identity *is* its content, so an UPDATE that
changed either would make the row lie about the other — a blob whose bytes no
longer hash to its address, which is exactly the silent-corruption shape the
hash exists to prevent. Superseded dossiers are never deleted either: a batch
pointer simply moves forward to a new address while the old evidence stays
reachable, the same way superseded results stay on record.

No sequence grant: ``sha256`` is a natural primary key and the table has no
autoincrement.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "6f4c9a2be7d1"
down_revision: str | None = "d38f8b50f074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLE = "stored_blob"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT ON TABLE {TABLE} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
