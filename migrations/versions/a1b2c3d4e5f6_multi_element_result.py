"""multi element result table

Revision ID: a1b2c3d4e5f6
Revises: c152c0ac35dc
Create Date: 2026-08-27

Multi-element ICP results live one row per element per sample per digest
method, append-only like fire_assay_result. The grade is stored as a mass
fraction (ppm, ppb) — the final number a certificate will quote — not the
raw instrument reading in mg/L.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "c152c0ac35dc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "multi_element_result",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column(
            "element",
            sa.Enum(
                "Ag", "Al", "As", "Au", "B", "Ba", "Be", "Bi", "Ca", "Cd",
                "Co", "Cr", "Cu", "Fe", "Ga", "Ge", "Hg", "In", "K", "La",
                "Li", "Mg", "Mn", "Mo", "Na", "Nb", "Ni", "P", "Pb", "Pd",
                "Pt", "Re", "S", "Sb", "Sc", "Se", "Sn", "Sr", "Ta", "Te",
                "Th", "Ti", "Tl", "U", "V", "W", "Y", "Yb", "Zn", "Zr",
                name="element_type",
                native_enum=False,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("grade_value", sa.Numeric(), nullable=False),
        sa.Column("grade_unit", sa.String(length=16), server_default="ppm", nullable=False),
        sa.Column("detection_limit", sa.Numeric(), nullable=True),
        sa.Column(
            "digest_method",
            sa.Enum(
                "aqua_regia", "four_acid", "peroxide_fusion",
                name="digest_method_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("method_notes", sa.Text(), nullable=True),
        sa.Column("analyst_id", sa.Integer(), nullable=False),
        sa.Column("analysed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("superseded_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_multi_element_result")),
    )
    op.create_index(
        op.f("ix_multi_element_result_sample_id"),
        "multi_element_result",
        ["sample_id"],
        unique=False,
    )
    op.create_unique_constraint(
        op.f("uq_multi_element_sample_element_digest"),
        "multi_element_result",
        ["sample_id", "element", "digest_method"],
    )
    op.create_foreign_key(
        op.f("fk_multi_element_result_supersedes_id"),
        "multi_element_result",
        "multi_element_result",
        ["supersedes_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_multi_element_result_supersession_states_reason"),
        "multi_element_result",
        "supersedes_id IS NULL OR "
        "(superseded_reason IS NOT NULL AND length(trim(superseded_reason)) > 0)",
    )
    op.create_check_constraint(
        op.f("ck_multi_element_result_grade_non_negative"),
        "multi_element_result",
        "grade_value >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_multi_element_result_grade_non_negative"),
        "multi_element_result",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_multi_element_result_supersession_states_reason"),
        "multi_element_result",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_multi_element_result_supersedes_id"),
        "multi_element_result",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_multi_element_sample_element_digest"),
        "multi_element_result",
        type_="unique",
    )
    op.drop_index(op.f("ix_multi_element_result_sample_id"), table_name="multi_element_result")
    op.drop_table("multi_element_result")
