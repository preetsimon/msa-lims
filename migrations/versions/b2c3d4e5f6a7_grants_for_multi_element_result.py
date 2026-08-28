"""append-only grants for multi_element_result

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-27

Append-only, matching fire_assay_result: a corrected element reading is a
new row whose supersedes_id points at the one it corrects, never an UPDATE
to the original.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLE = "multi_element_result"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT ON TABLE {TABLE} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON SEQUENCE {TABLE}_id_seq FROM {APP_ROLE}")
