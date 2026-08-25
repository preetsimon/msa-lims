"""grants for furnace batching

Revision ID: e4d969607d8c
Revises: f9845a996c0d
Create Date: 2026-08-25

``flux_recipe``, ``batch`` and ``crucible`` are mutable, not append-only,
joining ``b1d0c4e77a10``'s ``MUTABLE_TABLES`` list rather than its
append-only one. A recipe is edited in place as reagent sourcing changes,
matching ``instrument``; a batch's and a crucible's ``status`` columns
advance in place through :mod:`msa_lims.domain.batch_lifecycle`, matching
``sample.status``. What must never be an ``UPDATE`` is a crucible's frozen
charge amounts once weighed out — that discipline is enforced in
``batches/service.py``, not by a grant, the same way ``sample.status`` is
protected by the service layer and not by revoking UPDATE from the whole
table.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e4d969607d8c"
down_revision: str | None = "f9845a996c0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLES = ("flux_recipe", "batch", "crucible")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}")
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {table}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
        op.execute(f"REVOKE ALL ON SEQUENCE {table}_id_seq FROM {APP_ROLE}")
