"""append-only grants for fire_assay_result

Revision ID: 9a1c2e6f4b3d
Revises: 450d413603cf
Create Date: 2026-08-25

The comment in ``b1d0c4e77a10`` named this moment directly: "As results,
certificates and their amendments arrive in later phases they join this
tuple, not the one above." ``fire_assay_result`` is the first of those.

There is no ``ALTER DEFAULT PRIVILEGES`` in the earlier migration for exactly
this reason — without this file, ``fire_assay_result`` would be a table the
application role cannot see at all, and that failure would be loud and
immediate on the first request rather than a table that quietly turned out to
be editable. This migration is the reviewable decision the earlier one's
comment promised: append-only, matching ``audit_event``, because a corrected
fire assay result is a new row referencing the one it supersedes, never an
``UPDATE`` to the original.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9a1c2e6f4b3d"
down_revision: str | None = "450d413603cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLE = "fire_assay_result"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT ON TABLE {TABLE} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON SEQUENCE {TABLE}_id_seq FROM {APP_ROLE}")
