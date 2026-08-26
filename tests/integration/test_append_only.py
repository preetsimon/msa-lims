"""The append-only guarantee, proven against the real application role.

Every other test in this suite could pass with the grants missing. This one is
the reason the grants exist: it takes the same credentials the deployed
application uses and tries to rewrite history with them.

If someone later adds ``ALTER DEFAULT PRIVILEGES`` to a migration for
convenience, or grants UPDATE on ``audit_event`` to fix a bug, these tests fail
and say why.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def an_audit_event(owner_engine: Engine) -> Iterator[int]:
    """Insert one event as the owner, so there is something to try to tamper with."""
    with owner_engine.begin() as connection:
        event_id = connection.execute(
            text(
                "INSERT INTO audit_event (table_name, record_id, action, reason, entry_hash) "
                "VALUES ('sample', 1, 'create', 'received from client', repeat('a', 64)) "
                "RETURNING id"
            )
        ).scalar_one()
    yield int(event_id)
    with owner_engine.begin() as connection:
        connection.execute(text("DELETE FROM audit_event WHERE id = :id"), {"id": event_id})


class TestTheApplicationRoleCannotRewriteHistory:
    def test_it_can_read_audit_events(self, app_session: Session, an_audit_event: int) -> None:
        found = app_session.execute(
            text("SELECT action FROM audit_event WHERE id = :id"), {"id": an_audit_event}
        ).scalar_one()
        assert found == "create"

    def test_it_can_append_a_new_event(self, app_session: Session) -> None:
        app_session.execute(
            text(
                "INSERT INTO audit_event (table_name, record_id, action, entry_hash) "
                "VALUES ('sample', 99, 'transition', repeat('a', 64))"
            )
        )

    def test_it_cannot_update_one(self, app_session: Session, an_audit_event: int) -> None:
        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("UPDATE audit_event SET action = 'tampered' WHERE id = :id"),
                {"id": an_audit_event},
            )

    def test_it_cannot_delete_one(self, app_session: Session, an_audit_event: int) -> None:
        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("DELETE FROM audit_event WHERE id = :id"), {"id": an_audit_event}
            )

    def test_it_cannot_truncate_the_table(self, app_session: Session) -> None:
        """TRUNCATE is a separate privilege from DELETE, and forgetting it would
        leave a way to erase the log wholesale."""
        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(text("TRUNCATE audit_event"))

    def test_it_cannot_move_the_migration_version(self, app_session: Session) -> None:
        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(text("UPDATE alembic_version SET version_num = 'forged'"))


class TestOrdinaryTablesRemainMutable:
    """The grants are targeted, not blanket — a client's phone number must still
    be correctable."""

    def test_a_client_can_be_inserted_and_updated(self, app_session: Session) -> None:
        client_id = app_session.execute(
            text(
                "INSERT INTO client (code, name, is_active, created_at) "
                "VALUES ('TST', 'Test Mining Co', true, now()) RETURNING id"
            )
        ).scalar_one()
        app_session.execute(
            text("UPDATE client SET phone = '555-0100' WHERE id = :id"), {"id": client_id}
        )
        phone = app_session.execute(
            text("SELECT phone FROM client WHERE id = :id"), {"id": client_id}
        ).scalar_one()
        assert phone == "555-0100"

    def test_a_batch_status_can_be_updated(self, app_session: Session) -> None:
        """``batch`` and ``crucible`` (added for furnace batching) advance in
        place through ``domain.batch_lifecycle``, same tier as ``client``."""
        user_id = app_session.execute(
            text(
                "INSERT INTO lab_user (subject, email, full_name, role, is_active, created_at) "
                "VALUES ('sub-append-only-1', 'ao1@lab.test', 'A. Ppend', 'analyst', true, now()) "
                "RETURNING id"
            )
        ).scalar_one()
        batch_id = app_session.execute(
            text(
                "INSERT INTO batch (batch_number, status, opened_by_id, opened_at, created_at) "
                "VALUES ('BATCH-2026-9001', 'pending', :user_id, now(), now()) RETURNING id"
            ),
            {"user_id": user_id},
        ).scalar_one()
        app_session.execute(
            text("UPDATE batch SET status = 'charging' WHERE id = :id"), {"id": batch_id}
        )
        status = app_session.execute(
            text("SELECT status FROM batch WHERE id = :id"), {"id": batch_id}
        ).scalar_one()
        assert status == "charging"


class TestTheAmendmentReasonConstraint:
    def test_an_amendment_without_a_reason_is_refused_by_the_database(
        self, app_session: Session
    ) -> None:
        """A corrected result with no stated reason is the most common finding
        in a laboratory audit, so it is a CHECK rather than a service-layer
        convention."""
        with pytest.raises(IntegrityError, match="amendment_states_reason"):
            app_session.execute(
                text(
                    "INSERT INTO audit_event (table_name, record_id, action, entry_hash) "
                    "VALUES ('fire_assay_result', 5, 'amend', repeat('a', 64))"
                )
            )

    def test_whitespace_is_not_a_reason(self, app_session: Session) -> None:
        with pytest.raises(IntegrityError, match="amendment_states_reason"):
            app_session.execute(
                text(
                    "INSERT INTO audit_event (table_name, record_id, action, reason, entry_hash) "
                    "VALUES ('fire_assay_result', 5, 'amend', '   ', repeat('a', 64))"
                )
            )

    def test_an_amendment_with_a_reason_is_accepted(self, app_session: Session) -> None:
        app_session.execute(
            text(
                "INSERT INTO audit_event (table_name, record_id, action, reason, entry_hash) "
                "VALUES ('fire_assay_result', 5, 'amend', 'transcription error at the balance', "
                "repeat('a', 64))"
            )
        )
