"""Index prep_record.supersedes_id for chain traversal

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-09-12

Walks the supersession chain newest-first; an index here keeps
``WHERE supersedes_id = :id`` fast as the table grows.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a8"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_prep_record_supersedes_id",
        "prep_record",
        ["supersedes_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_prep_record_supersedes_id", table_name="prep_record")
