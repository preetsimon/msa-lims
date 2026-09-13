"""sentinel submission record

Revision ID: f1a2b3c4d5e6
Revises: ec9746b51b78
Create Date: 2026-08-28

Records what was sent to QC Sentinel, when, and what came back.  The table
is append-only: every submission is a fact, and failed attempts are worth
keeping (they carry the http_status that explains why the verdict is empty).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "ec9746b51b78"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sentinel_submission",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("sentinel_import_id", sa.Text(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("submitted_by", sa.BigInteger(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verdict", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["batch.id"],
            name=op.f("fk_sentinel_submission_batch_id_batch"),
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"],
            ["lab_user.id"],
            name=op.f("fk_sentinel_submission_submitted_by_lab_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sentinel_submission")),
    )
    op.create_index(
        op.f("ix_sentinel_submission_batch_id"),
        "sentinel_submission",
        ["batch_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sentinel_submission_submitted_at"),
        "sentinel_submission",
        ["submitted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_sentinel_submission_submitted_at"),
        table_name="sentinel_submission",
    )
    op.drop_index(
        op.f("ix_sentinel_submission_batch_id"),
        table_name="sentinel_submission",
    )
    op.drop_table("sentinel_submission")
