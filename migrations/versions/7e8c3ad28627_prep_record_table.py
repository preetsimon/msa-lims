"""prep_record_table

Revision ID: 7e8c3ad28627
Revises: c30ff731e024
Create Date: 2026-09-12 17:07:50.723626
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e8c3ad28627"
down_revision: str | None = "c30ff731e024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prep_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sample_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "stage",
            sa.Enum(
                "primary_crush",
                "secondary_crush",
                "split",
                "pulverize",
                "sieve",
                name="prep_stage",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("instrument_id", sa.BigInteger(), nullable=True),
        sa.Column("prep_tech_id", sa.BigInteger(), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_weight_g", sa.Numeric(), nullable=True),
        sa.Column("output_weight_g", sa.Numeric(), nullable=True),
        sa.Column("supersedes_id", sa.BigInteger(), nullable=True),
        sa.Column("superseded_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "input_weight_g IS NULL OR input_weight_g > 0",
            name=op.f("ck_prep_record_prep_input_weight_positive"),
        ),
        sa.CheckConstraint(
            "output_weight_g IS NULL OR output_weight_g > 0",
            name=op.f("ck_prep_record_prep_output_weight_positive"),
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR "
            "(superseded_reason IS NOT NULL AND length(trim(superseded_reason)) > 0)",
            name=op.f("ck_prep_record_prep_supersession_states_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instrument.id"],
            name=op.f("fk_prep_record_instrument_id_instrument"),
        ),
        sa.ForeignKeyConstraint(
            ["prep_tech_id"],
            ["lab_user.id"],
            name=op.f("fk_prep_record_prep_tech_id_lab_user"),
        ),
        sa.ForeignKeyConstraint(
            ["sample_id"],
            ["sample.id"],
            name=op.f("fk_prep_record_sample_id_sample"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["prep_record.id"],
            name=op.f("fk_prep_record_supersedes_id_prep_record"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prep_record")),
    )
    op.create_index(
        op.f("ix_prep_record_created_at"),
        "prep_record",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prep_record_instrument_id"),
        "prep_record",
        ["instrument_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prep_record_sample_id"),
        "prep_record",
        ["sample_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_prep_record_sample_id"), table_name="prep_record")
    op.drop_index(op.f("ix_prep_record_instrument_id"), table_name="prep_record")
    op.drop_index(op.f("ix_prep_record_created_at"), table_name="prep_record")
    op.drop_table("prep_record")
