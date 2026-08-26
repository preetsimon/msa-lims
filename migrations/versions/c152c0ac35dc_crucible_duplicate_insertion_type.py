"""crucible duplicate insertion type

Revision ID: c152c0ac35dc
Revises: 6f4c9a2be7d1
Create Date: 2026-08-26 03:48:39.430748

Duplicates re-insert an existing *sample* into an extra crucible, so the flat
``UNIQUE(batch_id, sample_id)`` had to become a partial unique index over
primary charges only — a duplicate deliberately shares its sample with the
primary slot, that is what a duplicate is. ``insertion_type`` carries the
three duplicate members of ``QcMaterialType`` and nothing else (its own
VARCHAR CHECK), paired by constraint so only a sample-naming crucicle can
hold one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c152c0ac35dc"
down_revision: str | None = "6f4c9a2be7d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crucible",
        sa.Column(
            "insertion_type",
            sa.Enum(
                "field_duplicate",
                "prep_duplicate",
                "pulp_duplicate",
                name="crucible_insertion_type",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
    )
    op.drop_constraint(op.f("batch_sample"), "crucible", type_="unique")
    op.create_index(
        "uq_crucible_batch_primary_sample",
        "crucible",
        ["batch_id", "sample_id"],
        unique=True,
        postgresql_where=sa.text("insertion_type IS NULL"),
    )
    op.create_check_constraint(
        op.f("ck_crucible_insertion_type_requires_sample_only"),
        "crucible",
        "insertion_type IS NULL OR (sample_id IS NOT NULL AND qc_material_id IS NULL)",
    )


def downgrade() -> None:
    # Reversing this migration requires the table to hold no duplicate rows:
    # a duplicate shares its sample with the primary charge, which the old
    # flat UNIQUE forbids. Deleting recorded evidence to make a schema fit is
    # never automatic — clear duplicates explicitly first, if ever.
    op.drop_constraint(
        op.f("ck_crucible_insertion_type_requires_sample_only"), "crucible", type_="check"
    )
    op.drop_index(
        "uq_crucible_batch_primary_sample",
        table_name="crucible",
        postgresql_where=sa.text("insertion_type IS NULL"),
    )
    op.create_unique_constraint(
        op.f("batch_sample"), "crucible", ["batch_id", "sample_id"]
    )
    op.drop_column("crucible", "insertion_type")
