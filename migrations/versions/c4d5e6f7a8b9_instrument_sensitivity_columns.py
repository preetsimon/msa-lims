"""Instrument balance sensitivity and detection limit columns

Revision ID: c4d5e6f7a8b9
Revises: b2c3d4e5f6a8
Create Date: 2026-09-12

Adds nullable numeric columns balance_sensitivity_mg and
solution_detection_limit to the instrument table.  These let the
instrument registry carry the values that determine whether a result is a
non-detect, replacing per-request guesses in result entry.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b2c3d4e5f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"


def upgrade() -> None:
    op.add_column(
        "instrument",
        sa.Column("balance_sensitivity_mg", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "instrument",
        sa.Column("solution_detection_limit", sa.Numeric(), nullable=True),
    )

    op.execute(f"GRANT SELECT, INSERT ON instrument TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_column("instrument", "solution_detection_limit")
    op.drop_column("instrument", "balance_sensitivity_mg")
