"""append-only grants for certificate_multi_element_result

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-27

Append-only grants for the certificate_multi_element_result join table,
matching the existing certificate grants pattern (SELECT + INSERT, no
UPDATE/DELETE). The table is written once at certificate issuance and
never touched again.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLE = "certificate_multi_element_result"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, INSERT ON TABLE {TABLE} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE ALL ON TABLE {TABLE} FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON SEQUENCE {TABLE}_id_seq FROM {APP_ROLE}")
