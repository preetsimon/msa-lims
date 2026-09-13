"""append-only grants for prep_record

Revision ID: a1b2c3d4e5f7
Revises: 7e8c3ad28627
Create Date: 2026-09-12

Append-only: SELECT + INSERT only, no UPDATE or DELETE. Corrected prep
records are new rows with ``supersedes_id`` pointing at the row they
replace, never UPDATEs to the original.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "7e8c3ad28627"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLE = "prep_record"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT ON TABLE {TABLE} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON SEQUENCE {TABLE}_id_seq FROM {APP_ROLE}")
