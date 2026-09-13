"""multi element grade censored

Revision ID: ec9746b51b78
Revises: 6cf291cb08e0
Create Date: 2026-08-28

Adds grade_censored boolean to multi_element_result, following the same
pattern as fire_assay_result.au_censored. When a reading is at or below its
detection limit, grade_censored=True and grade_value holds the limit.

Append-only grants already cover new columns on this table — no companion
grants migration needed (see c3d4e5f6a7b8's docstring for the precedent).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ec9746b51b78"
down_revision: str | None = "6cf291cb08e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "multi_element_result",
        sa.Column(
            "grade_censored",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("multi_element_result", "grade_censored")
