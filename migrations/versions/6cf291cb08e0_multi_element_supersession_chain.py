"""multi element supersession chain

Revision ID: 6cf291cb08e0
Revises: e5f6a7b8c9d0
Create Date: 2026-08-28

Drops the UNIQUE(sample_id, element, digest_method) constraint that prevented
supersession — a correcting row must repeat the same triple, which the
constraint refused. Replaces it with a partial unique index on supersedes_id:
a row may be superseded at most once, making a branching chain structurally
impossible rather than merely refused in Python.

Note: downgrade() restores the original constraint, which cannot hold once any
correction exists in the data. This is true from empty, which is all CI's
downgrade check verifies, but would fail against a database with real
supersession rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6cf291cb08e0"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_multi_element_sample_element_digest",
        "multi_element_result",
        type_="unique",
    )
    op.create_index(
        "uq_mer_one_successor_per_row",
        "multi_element_result",
        ["supersedes_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_mer_one_successor_per_row", table_name="multi_element_result")
    op.create_unique_constraint(
        "uq_multi_element_sample_element_digest",
        "multi_element_result",
        ["sample_id", "element", "digest_method"],
    )
