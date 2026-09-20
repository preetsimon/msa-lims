"""Add client_id FK to lab_user for per-client row scoping

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-12

Adds a nullable client_id foreign key to lab_user, linking client-role
users to the client they represent.  When client_id is set, reads are
scoped to that client's samples and certificates.  When NULL, the user
is lab staff with unrestricted access.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"


def upgrade() -> None:
    op.add_column(
        "lab_user",
        sa.Column("client_id", sa.BigInteger, nullable=True),
    )
    op.create_index(
        "ix_lab_user_client_id",
        "lab_user",
        ["client_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_lab_user_client_id_client",
        "lab_user",
        "client",
        ["client_id"],
        ["id"],
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON lab_user TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_constraint("fk_lab_user_client_id_client", "lab_user", type_="foreignkey")
    op.drop_index("ix_lab_user_client_id", table_name="lab_user")
    op.drop_column("lab_user", "client_id")
