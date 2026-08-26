"""qc dossier blob store and batch pointer

Revision ID: d38f8b50f074
Revises: 63137adc5266
Create Date: 2026-08-25 23:41:14.069007

``stored_blob`` is the content-addressed evidence store PROGRESS.md's open
questions have named since Phase 1; QC dossiers are the second consumer that
justifies building it (the certificate PDF was always the first). Its rows
are addressed by their own sha256 and are write-once — the mutability
decision belongs to the *grants* migration that follows this one, per this
repo's schema/mutability split (b1d0c4e77a10's rule), not to table creation.

The two ``batch`` columns are a pointer to the batch's current sealed QC
dossier plus when it was generated. Schema-only with no grants companion:
``batch`` is already mutable-tier, and Postgres grants are table-level.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d38f8b50f074"
down_revision: str | None = "63137adc5266"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_blob",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_stored_blob")),
    )
    op.create_index(op.f("ix_stored_blob_created_at"), "stored_blob", ["created_at"], unique=False)
    op.add_column("batch", sa.Column("qc_dossier_sha256", sa.String(length=64), nullable=True))
    op.add_column(
        "batch",
        sa.Column("qc_dossier_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch", "qc_dossier_generated_at")
    op.drop_column("batch", "qc_dossier_sha256")
    op.drop_index(op.f("ix_stored_blob_created_at"), table_name="stored_blob")
    op.drop_table("stored_blob")
