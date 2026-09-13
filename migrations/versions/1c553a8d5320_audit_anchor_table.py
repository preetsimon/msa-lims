"""audit_anchor table

Revision ID: 1c553a8d5320
Revises: b3c4d5e6f7a8
Create Date: 2026-08-31

Append-only table for OpenTimestamps proofs of the audit chain head.
Same grant tier as audit_event, fire_assay_result, and sentinel_submission:
SELECT + INSERT for the application role, no UPDATE, no DELETE.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1c553a8d5320"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLE = "audit_anchor"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("anchored_hash", sa.String(64), nullable=False),
        sa.Column("chain_event_id", sa.BigInteger, nullable=False),
        sa.Column("ots_proof", sa.LargeBinary, nullable=False),
        sa.Column("ots_proof_sha256", sa.String(64), nullable=False),
        sa.Column("anchored_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        f"ix_{TABLE}_created_at", TABLE, ["created_at"], unique=False
    )

    # Append-only grants
    op.execute(f"GRANT SELECT, INSERT ON TABLE {TABLE} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {TABLE}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT ON TABLE {TABLE} FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE, SELECT ON SEQUENCE {TABLE}_id_seq FROM {APP_ROLE}")
    op.drop_index(f"ix_{TABLE}_created_at", table_name=TABLE)
    op.drop_table(TABLE)
