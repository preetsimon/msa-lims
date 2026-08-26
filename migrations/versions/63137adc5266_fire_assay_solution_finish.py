"""fire assay solution finish

Revision ID: 63137adc5266
Revises: 9e1d03d83772
Create Date: 2026-08-25 22:49:04.104247
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "63137adc5266"
down_revision: str | None = "9e1d03d83772"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The two halves of the finish rule, kept as constants so ``upgrade`` states
#: each exactly once and the intent is readable without parsing SQL twice.
_GRAVIMETRIC_WEIGHS_A_BEAD = (
    "method <> 'fire_assay_gravimetric' OR "
    "(gold_bead_mg IS NOT NULL AND solution_concentration IS NULL "
    "AND solution_volume_ml IS NULL AND solution_concentration_unit IS NULL)"
)
_SOLUTION_FINISH_READS_A_CONCENTRATION = (
    "method = 'fire_assay_gravimetric' OR "
    "(solution_concentration IS NOT NULL AND solution_volume_ml IS NOT NULL "
    "AND solution_concentration_unit IS NOT NULL AND gold_bead_mg IS NULL)"
)


def upgrade() -> None:
    # Schema-only by design, like cfef85e840d4: Postgres grants are table-level,
    # so msa_app's existing SELECT/INSERT on the append-only fire_assay_result
    # already covers these columns. Adding columns to an already-decided table
    # is not the mutability decision b1d0c4e77a10 requires for new *tables*.
    op.add_column(
        "fire_assay_result", sa.Column("solution_concentration", sa.Numeric(), nullable=True)
    )
    op.add_column(
        "fire_assay_result",
        sa.Column("solution_concentration_unit", sa.String(length=16), nullable=True),
    )
    op.add_column("fire_assay_result", sa.Column("solution_volume_ml", sa.Numeric(), nullable=True))
    op.add_column(
        "fire_assay_result", sa.Column("solution_detection_limit", sa.Numeric(), nullable=True)
    )

    # gold_bead_mg stops being universally required: an AAS/ICP-MS row has no
    # bead weight to record, because the bead was dissolved rather than weighed.
    # The CHECK constraints below are what keep it mandatory where it still
    # applies — the column's nullability alone would be a loosening.
    op.alter_column("fire_assay_result", "gold_bead_mg", existing_type=sa.NUMERIC(), nullable=True)

    op.create_check_constraint(
        op.f("ck_fire_assay_result_gravimetric_weighs_a_bead"),
        "fire_assay_result",
        _GRAVIMETRIC_WEIGHS_A_BEAD,
    )
    op.create_check_constraint(
        op.f("ck_fire_assay_result_solution_finish_reads_a_concentration"),
        "fire_assay_result",
        _SOLUTION_FINISH_READS_A_CONCENTRATION,
    )
    op.create_check_constraint(
        op.f("ck_fire_assay_result_solution_concentration_non_negative"),
        "fire_assay_result",
        "solution_concentration >= 0",
    )
    op.create_check_constraint(
        op.f("ck_fire_assay_result_solution_volume_positive"),
        "fire_assay_result",
        "solution_volume_ml > 0",
    )


def downgrade() -> None:
    """Reversible from empty, and deliberately *not* reversible over real data.

    Restoring ``gold_bead_mg NOT NULL`` fails outright if any AAS/ICP-MS row
    exists, because those rows genuinely have no bead weight — there was never
    a bead to weigh. That failure is the correct outcome and is left
    unhandled: the alternatives are inventing a bead weight for a measurement
    nobody made, or deleting real results to make a schema change fit. A lab
    that truly needs to go back has to say what should happen to those rows.
    """
    op.drop_constraint(
        op.f("ck_fire_assay_result_solution_volume_positive"), "fire_assay_result", type_="check"
    )
    op.drop_constraint(
        op.f("ck_fire_assay_result_solution_finish_reads_a_concentration"),
        "fire_assay_result",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_fire_assay_result_solution_concentration_non_negative"),
        "fire_assay_result",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_fire_assay_result_gravimetric_weighs_a_bead"), "fire_assay_result", type_="check"
    )
    op.alter_column("fire_assay_result", "gold_bead_mg", existing_type=sa.NUMERIC(), nullable=False)
    op.drop_column("fire_assay_result", "solution_detection_limit")
    op.drop_column("fire_assay_result", "solution_volume_ml")
    op.drop_column("fire_assay_result", "solution_concentration_unit")
    op.drop_column("fire_assay_result", "solution_concentration")
