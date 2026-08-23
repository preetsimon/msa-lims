"""append-only grants for the restricted application role

Revision ID: b1d0c4e77a10
Revises: a64c168cff52
Create Date: 2026-08-22

Creates ``msa_app``, the role the application actually connects as, and grants
it exactly what it needs — no more.

The point of this migration is one sentence: **the application cannot rewrite
history, and that is true because Postgres will not let it, not because the
service layer remembers not to.** ``audit_event`` is granted SELECT and INSERT
and nothing else. An UPDATE against it fails with a permission error even from a
psql session using the application's credentials, and an integration test proves
exactly that.

Two consequences worth knowing before you add a table:

* **New tables are inaccessible until granted.** There is deliberately no
  ``ALTER DEFAULT PRIVILEGES`` here. A table that appears in a later migration
  gets no grants automatically, so whoever adds it has to decide — in the
  migration, in the diff, where it will be reviewed — whether it is mutable or
  append-only. Discovering the omission is a loud failure on first use; the
  alternative is a results table that quietly turned out to be editable.
* **Migrations do not run as this role.** They run as the schema owner, because
  an owner bypasses grants and therefore could never be constrained by them.
  That split is why ``database_url`` and ``migration_database_url`` are separate
  settings.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b1d0c4e77a10"
down_revision: str | None = "a64c168cff52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "msa_app"

#: Ordinary reference and workflow data. A client changes its phone number and a
#: sample changes status, so these carry the full set of grants.
MUTABLE_TABLES = (
    "client",
    "project",
    "drill_hole",
    "submission",
    "sample",
    "instrument",
    "lab_user",
)

#: Written once and never touched again. As results, certificates and their
#: amendments arrive in later phases they join this tuple, not the one above.
APPEND_ONLY_TABLES = ("audit_event",)


def upgrade() -> None:
    # CREATE ROLE is not idempotent and has no IF NOT EXISTS, so it is guarded.
    # The password is a development default; a deployment sets a real one and
    # this block then only ensures the role exists.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE}';
            END IF;
        END
        $$;
        """
    )

    op.execute(f"GRANT CONNECT ON DATABASE {_current_database()} TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    for table in MUTABLE_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}")

    for table in APPEND_ONLY_TABLES:
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO {APP_ROLE}")

    # Identity columns need their sequences. Granting USAGE on each sequence
    # individually rather than schema-wide keeps the "nothing is granted by
    # default" property intact for sequences too.
    for table in MUTABLE_TABLES + APPEND_ONLY_TABLES:
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {table}_id_seq TO {APP_ROLE}")

    # The alembic bookkeeping table is readable so the application can report
    # which migration it is running against, but not writable: only migrations
    # move the version, and migrations do not run as this role.
    op.execute(f"GRANT SELECT ON TABLE alembic_version TO {APP_ROLE}")


def downgrade() -> None:
    for table in MUTABLE_TABLES + APPEND_ONLY_TABLES:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")
        op.execute(f"REVOKE ALL ON SEQUENCE {table}_id_seq FROM {APP_ROLE}")
    op.execute(f"REVOKE ALL ON TABLE alembic_version FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    # The role itself is left in place. Dropping it would fail wherever it still
    # owns an object or holds a grant in another database, and a dangling login
    # with no privileges is harmless where a half-completed downgrade is not.


def _current_database() -> str:
    """Quote the database name the migration is connected to.

    Read from the live connection rather than from settings, because the test
    harness runs these same migrations against ``msa_test``.
    """
    name = op.get_bind().engine.url.database
    assert name is not None
    return f'"{name}"'
