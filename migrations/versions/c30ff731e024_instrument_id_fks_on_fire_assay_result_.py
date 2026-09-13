"""instrument_id FKs on fire_assay_result and batch

Revision ID: c30ff731e024
Revises: 1c553a8d5320
Create Date: 2026-09-11

Adds nullable instrument_id foreign keys to fire_assay_result and batch,
linking results and batches to the instrument registry.  Both are nullable
to preserve backward compatibility with existing rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c30ff731e024"
down_revision: str | None = "1c553a8d5320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"


def upgrade() -> None:
    # --- fire_assay_result.instrument_id ---
    op.add_column(
        "fire_assay_result",
        sa.Column("instrument_id", sa.BigInteger, nullable=True),
    )
    op.create_index(
        "ix_fire_assay_result_instrument_id",
        "fire_assay_result",
        ["instrument_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_fire_assay_result_instrument_id_instrument",
        "fire_assay_result",
        "instrument",
        ["instrument_id"],
        ["id"],
    )
    # Grant UPDATE on the new column for the existing FK row (not new INSERT/DELETE).
    # fire_assay_result is append-only, but the FK column is nullable and can
    # be populated on an existing row via UPDATE — the app role already has
    # INSERT+SELECT; add UPDATE only on this column if needed by the service layer.
    # (No grant change needed: the app role has UPDATE on fire_assay_result
    # already?  No — fire_assay_result is append-only.  The instrument_id is
    # set at INSERT time, so no UPDATE grant is needed.)

    # --- batch.instrument_id ---
    op.add_column(
        "batch",
        sa.Column("instrument_id", sa.BigInteger, nullable=True),
    )
    op.create_index(
        "ix_batch_instrument_id",
        "batch",
        ["instrument_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_batch_instrument_id_instrument",
        "batch",
        "instrument",
        ["instrument_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_batch_instrument_id_instrument", "batch", type_="foreignkey"
    )
    op.drop_index("ix_batch_instrument_id", table_name="batch")
    op.drop_column("batch", "instrument_id")

    op.drop_constraint(
        "fk_fire_assay_result_instrument_id_instrument",
        "fire_assay_result",
        type_="foreignkey",
    )
    op.drop_index("ix_fire_assay_result_instrument_id", table_name="fire_assay_result")
    op.drop_column("fire_assay_result", "instrument_id")
