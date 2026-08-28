"""silver by difference columns on fire_assay_result

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-27

Adds nullable silver columns to fire_assay_result. Silver by difference is
computed from dore_bead_mg - gold_bead_mg when both are present; null on a
solution finish (no bead to part) or when only gold_bead_mg was entered.
Schema-only: the existing append-only grants on fire_assay_result already
cover new columns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fire_assay_result", sa.Column("silver_bead_mg", sa.Numeric(), nullable=True))
    op.add_column("fire_assay_result", sa.Column("silver_value", sa.Numeric(), nullable=True))
    op.add_column(
        "fire_assay_result", sa.Column("silver_detection_limit", sa.Numeric(), nullable=True)
    )
    op.add_column("fire_assay_result", sa.Column("silver_censored", sa.Boolean(), nullable=True))
    op.add_column(
        "fire_assay_result", sa.Column("silver_unit", sa.String(length=16), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_fire_assay_result_silver_bead_non_negative"),
        "fire_assay_result",
        "silver_bead_mg IS NULL OR silver_bead_mg >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_fire_assay_result_silver_bead_non_negative"),
        "fire_assay_result",
        type_="check",
    )
    op.drop_column("fire_assay_result", "silver_unit")
    op.drop_column("fire_assay_result", "silver_censored")
    op.drop_column("fire_assay_result", "silver_detection_limit")
    op.drop_column("fire_assay_result", "silver_value")
    op.drop_column("fire_assay_result", "silver_bead_mg")
