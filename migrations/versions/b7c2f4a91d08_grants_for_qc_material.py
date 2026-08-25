"""grants for qc_material

Revision ID: b7c2f4a91d08
Revises: 1d81304e5265
Create Date: 2026-08-25

``qc_material`` is mutable, joining ``b1d0c4e77a10``'s ``MUTABLE_TABLES``
list: it is stock reference data, edited in place and retired via
``is_active`` rather than deletion — the same treatment ``flux_recipe`` and
``instrument`` already receive. The schema migration (``1d81304e5265``) and
this grants decision are separate, matching the discipline every other table
followed: whether the application role may rewrite rows is a decision made
in its own reviewable diff, never an accident of table creation.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b7c2f4a91d08"
down_revision: str | None = "1d81304e5265"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLE = "qc_material"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {TABLE} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON SEQUENCE {TABLE}_id_seq FROM {APP_ROLE}")
