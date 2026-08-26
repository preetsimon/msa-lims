"""The audit hash chain, against a real Postgres session: writing it
(`record_audit_event`) and independently verifying it (`verify_chain`).
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from msa_lims.db.audit import record_audit_event, verify_chain
from msa_lims.db.models import AuditEvent, LabUser
from msa_lims.domain.audit_chain import compute_entry_hash
from msa_lims.domain.enums import Role

pytestmark = pytest.mark.integration


@pytest.fixture
def actor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-audit-chain-1",
        email="chain@lab.test",
        full_name="C. Hain",
        role=Role.ANALYST,
    )
    app_session.add(user)
    app_session.flush()
    return user


class TestRecordAuditEvent:
    def test_the_first_row_is_its_own_genesis(self, app_session: Session, actor: LabUser) -> None:
        event = record_audit_event(
            app_session,
            table_name="client",
            record_id=1,
            action="create",
            actor_id=actor.id,
            after={"name": "Acme"},
        )
        app_session.flush()

        assert event.prev_entry_hash is None
        assert event.entry_hash == compute_entry_hash(
            prev_entry_hash=None,
            table_name="client",
            record_id=1,
            action="create",
            before=None,
            after={"name": "Acme"},
            reason=None,
            actor_id=actor.id,
            actor_ip=None,
        )

    def test_a_second_row_links_to_the_first(self, app_session: Session, actor: LabUser) -> None:
        first = record_audit_event(
            app_session,
            table_name="client",
            record_id=1,
            action="create",
            actor_id=actor.id,
            after={"name": "Acme"},
        )
        app_session.flush()

        second = record_audit_event(
            app_session,
            table_name="client",
            record_id=2,
            action="create",
            actor_id=actor.id,
            after={"name": "Beta"},
        )
        app_session.flush()

        assert second.prev_entry_hash == first.entry_hash
        assert second.entry_hash != first.entry_hash

    def test_a_chain_of_several_rows_links_end_to_end(
        self, app_session: Session, actor: LabUser
    ) -> None:
        events = [
            record_audit_event(
                app_session,
                table_name="client",
                record_id=i,
                action="create",
                actor_id=actor.id,
                after={"name": f"Row {i}"},
            )
            for i in range(5)
        ]
        app_session.flush()

        for earlier, later in pairwise(events):
            assert later.prev_entry_hash == earlier.entry_hash


class TestVerifyChain:
    def test_an_empty_table_verifies_trivially(self, app_session: Session) -> None:
        result = verify_chain(app_session)
        assert result.valid is True
        assert result.verified_count == 0
        assert result.head_hash is None
        assert result.first_break is None

    def test_a_genuine_chain_verifies(self, app_session: Session, actor: LabUser) -> None:
        last = None
        for i in range(4):
            last = record_audit_event(
                app_session,
                table_name="client",
                record_id=i,
                action="create",
                actor_id=actor.id,
                after={"name": f"Row {i}"},
            )
        app_session.flush()

        result = verify_chain(app_session)
        assert result.valid is True
        assert result.verified_count == 4
        assert last is not None
        assert result.head_hash == last.entry_hash

    def test_upto_stops_verification_early(self, app_session: Session, actor: LabUser) -> None:
        events = [
            record_audit_event(
                app_session,
                table_name="client",
                record_id=i,
                action="create",
                actor_id=actor.id,
                after={"name": f"Row {i}"},
            )
            for i in range(3)
        ]
        app_session.flush()

        result = verify_chain(app_session, upto=events[1].id)
        assert result.valid is True
        assert result.verified_count == 2
        assert result.head_hash == events[1].entry_hash

    def test_a_row_inserted_with_a_wrong_entry_hash_breaks_the_chain(
        self, app_session: Session, actor: LabUser
    ) -> None:
        """No UPDATE privilege needed for this one — a fresh INSERT with a
        deliberately wrong hash is a legal write under `msa_app`'s own
        grants, and is exactly what a bug (or a compromised application)
        writing a bad hash would look like from the database's point of
        view."""
        genuine = record_audit_event(
            app_session,
            table_name="client",
            record_id=1,
            action="create",
            actor_id=actor.id,
            after={"name": "Acme"},
        )
        app_session.flush()

        forged = AuditEvent(
            table_name="client",
            record_id=2,
            action="create",
            after={"name": "Forged"},
            actor_id=actor.id,
            prev_entry_hash=genuine.entry_hash,
            entry_hash="f" * 64,
        )
        app_session.add(forged)
        app_session.flush()

        result = verify_chain(app_session)
        assert result.valid is False
        assert result.verified_count == 1
        assert result.first_break is not None
        assert result.first_break.id == forged.id
        assert result.first_break.reason == "entry_hash does not match recomputation"

    def test_a_row_whose_prev_hash_points_at_the_wrong_predecessor_breaks_the_chain(
        self, app_session: Session, actor: LabUser
    ) -> None:
        record_audit_event(
            app_session,
            table_name="client",
            record_id=1,
            action="create",
            actor_id=actor.id,
            after={"name": "Acme"},
        )
        app_session.flush()

        orphan = AuditEvent(
            table_name="client",
            record_id=2,
            action="create",
            after={"name": "Orphan"},
            actor_id=actor.id,
            prev_entry_hash="a" * 64,
            entry_hash=compute_entry_hash(
                prev_entry_hash="a" * 64,
                table_name="client",
                record_id=2,
                action="create",
                before=None,
                after={"name": "Orphan"},
                reason=None,
                actor_id=actor.id,
                actor_ip=None,
            ),
        )
        app_session.add(orphan)
        app_session.flush()

        result = verify_chain(app_session)
        assert result.valid is False
        assert result.first_break is not None
        assert result.first_break.id == orphan.id
        assert "prev_entry_hash" in result.first_break.reason

    def test_a_direct_update_by_the_schema_owner_is_detected(
        self, app_session: Session, actor: LabUser, owner_engine: Engine
    ) -> None:
        """The scenario audit idea #1's own thesis names: `msa_app` cannot
        rewrite history (see `test_append_only.py`), but a more privileged
        role — a DBA, a support engineer with direct database access — can.
        The hash chain is what catches *that*.

        Needs a real commit, not just a flush: the tampering happens on a
        genuinely separate connection (`owner_engine`), which cannot see
        `app_session`'s uncommitted work at all under READ COMMITTED. Every
        row this test commits is deleted again in `finally`, through the
        same owner connection, so nothing outlives the test the way
        `test_append_only.py`'s own `an_audit_event` fixture already
        manages its one committed row.
        """
        event = record_audit_event(
            app_session,
            table_name="client",
            record_id=1,
            action="create",
            actor_id=actor.id,
            after={"name": "Acme"},
        )
        app_session.commit()
        try:
            with owner_engine.begin() as connection:
                connection.execute(
                    text("UPDATE audit_event SET after = :tampered WHERE id = :id"),
                    {"tampered": '{"name": "Tampered"}', "id": event.id},
                )

            result = verify_chain(app_session)
            assert result.valid is False
            assert result.first_break is not None
            assert result.first_break.id == event.id
        finally:
            with owner_engine.begin() as connection:
                connection.execute(text("DELETE FROM audit_event WHERE id = :id"), {"id": event.id})
                connection.execute(text("DELETE FROM lab_user WHERE id = :id"), {"id": actor.id})
