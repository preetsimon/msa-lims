"""append-only grants for certificate and certificate_result

Revision ID: 2d5f8a17c930
Revises: f73c45982855
Create Date: 2026-08-25

The second table to join the append-only tuple ``b1d0c4e77a10``'s comment
named — ``fire_assay_result`` was the first. ``certificate`` is append-only
for the same reason: an amended Certificate of Analysis is a new row whose
``supersedes_id`` points at the one it corrects, never an ``UPDATE`` to a
document a client may already hold.

``certificate_result`` — the join recording exactly which
``fire_assay_result`` row each certified sample froze at issuance — is
append-only too. It is written once, atomically, alongside its parent
certificate, and never touched again; granting it UPDATE/DELETE would let the
one thing a certificate promises (this is what was reported, and when)
quietly become untrue.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2d5f8a17c930"
down_revision: str | None = "f73c45982855"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"
TABLES = ("certificate", "certificate_result")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO {APP_ROLE}")
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {table}_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
        op.execute(f"REVOKE ALL ON SEQUENCE {table}_id_seq FROM {APP_ROLE}")
