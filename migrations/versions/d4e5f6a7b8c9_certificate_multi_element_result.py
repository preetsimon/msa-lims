"""certificate multi element result join table

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27

Freezes multi-element readings into a certificate at issuance time, same
principle as certificate_result for fire assay results. Grants follow the
existing certificate grants (mutable tier).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "certificate_multi_element_result",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("certificate_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("multi_element_result_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_certificate_multi_element_result")),
        sa.ForeignKeyConstraint(
            ["certificate_id"],
            ["certificate.id"],
            name=op.f("fk_certificate_multi_element_result_certificate_id"),
        ),
        sa.ForeignKeyConstraint(
            ["sample_id"],
            ["sample.id"],
            name=op.f("fk_certificate_multi_element_result_sample_id"),
        ),
        sa.ForeignKeyConstraint(
            ["multi_element_result_id"],
            ["multi_element_result.id"],
            name=op.f("fk_certificate_multi_element_result_multi_element_result_id"),
        ),
    )
    op.create_index(
        op.f("ix_certificate_multi_element_result_certificate_id"),
        "certificate_multi_element_result",
        ["certificate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_certificate_multi_element_result_certificate_id"),
        table_name="certificate_multi_element_result",
    )
    op.drop_table("certificate_multi_element_result")
