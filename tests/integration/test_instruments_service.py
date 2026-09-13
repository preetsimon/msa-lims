"""Instrument registry — service layer tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, LabUser
from msa_lims.domain.enums import InstrumentStatus, InstrumentType, Role
from msa_lims.instruments import (
    InstrumentInput,
    InstrumentNameConflictError,
    InstrumentNotFoundError,
    create_instrument,
    get_instrument,
    list_instruments,
    update_instrument,
)


@pytest.fixture()
def actor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="test-actor",
        email="test-actor@lab.invalid",
        full_name="Test Actor",
        role=Role.SUPERVISOR,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture()
def sample_instrument_data() -> InstrumentInput:
    return InstrumentInput(
        name="ICP-OES-01",
        instrument_type=InstrumentType.ICP_OES,
        manufacturer="Agilent",
        model="5800",
        serial_number="SN-12345",
        location="Lab B",
    )


def test_create_instrument(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    instrument = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)

    assert instrument.id is not None
    assert instrument.name == "ICP-OES-01"
    assert instrument.instrument_type == InstrumentType.ICP_OES
    assert instrument.manufacturer == "Agilent"
    assert instrument.model == "5800"
    assert instrument.serial_number == "SN-12345"
    assert instrument.location == "Lab B"
    assert instrument.status == InstrumentStatus.ACTIVE


def test_create_instrument_audit_event(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    instrument = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)

    event = app_session.scalar(
        select(AuditEvent).where(
            AuditEvent.table_name == "instrument",
            AuditEvent.record_id == instrument.id,
            AuditEvent.action == "create",
        )
    )
    assert event is not None
    assert event.actor_id == actor.id
    assert event.after is not None
    assert event.after["name"] == "ICP-OES-01"


def test_create_instrument_duplicate_name_raises(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)

    with pytest.raises(InstrumentNameConflictError):
        create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)


def test_get_instrument(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    created = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)
    fetched = get_instrument(app_session, created.id)
    assert fetched.id == created.id
    assert fetched.name == "ICP-OES-01"


def test_get_instrument_not_found(app_session: Session) -> None:
    with pytest.raises(InstrumentNotFoundError):
        get_instrument(app_session, 99999)


def test_list_instruments(app_session: Session, actor: LabUser) -> None:
    create_instrument(
        app_session,
        InstrumentInput(name="AAS-01", instrument_type=InstrumentType.ATOMIC_ABSORPTION),
        actor_id=actor.id,
        actor_role=actor.role,
    )
    create_instrument(
        app_session,
        InstrumentInput(name="ICP-01", instrument_type=InstrumentType.ICP_MS),
        actor_id=actor.id,
        actor_role=actor.role,
    )

    all_instruments = list_instruments(app_session)
    assert len(all_instruments) == 2

    aas_only = list_instruments(app_session, instrument_type=InstrumentType.ATOMIC_ABSORPTION)
    assert len(aas_only) == 1
    assert aas_only[0].name == "AAS-01"

    active_only = list_instruments(app_session, status=InstrumentStatus.ACTIVE)
    assert len(active_only) == 2


def test_update_instrument_location(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    instrument = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)
    updated = update_instrument(app_session, instrument.id, location="Lab C", actor_id=actor.id, actor_role=actor.role)

    assert updated.location == "Lab C"
    assert updated.name == "ICP-OES-01"  # unchanged


def test_update_instrument_status(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    instrument = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)
    updated = update_instrument(
        app_session, instrument.id, status=InstrumentStatus.MAINTENANCE, actor_id=actor.id, actor_role=actor.role
    )

    assert updated.status == InstrumentStatus.MAINTENANCE


def test_update_instrument_name_conflict(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)
    other = create_instrument(
        app_session,
        InstrumentInput(name="ICP-MS-01", instrument_type=InstrumentType.ICP_MS),
        actor_id=actor.id,
        actor_role=actor.role,
    )

    with pytest.raises(InstrumentNameConflictError):
        update_instrument(app_session, other.id, name="ICP-OES-01", actor_id=actor.id, actor_role=actor.role)


def test_update_instrument_audit_event(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    instrument = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)
    update_instrument(app_session, instrument.id, location="Lab C", actor_id=actor.id, actor_role=actor.role)

    events = list(
        app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "instrument",
                AuditEvent.record_id == instrument.id,
                AuditEvent.action == "amend",
            )
        )
    )
    assert len(events) == 1
    assert events[0].before is not None
    assert events[0].before["location"] == "Lab B"
    assert events[0].after is not None
    assert events[0].after["location"] == "Lab C"


def test_update_instrument_no_changes(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    instrument = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)
    updated = update_instrument(app_session, instrument.id, name="ICP-OES-01", actor_id=actor.id, actor_role=actor.role)
    assert updated.name == "ICP-OES-01"

    events = list(
        app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "instrument",
                AuditEvent.record_id == instrument.id,
                AuditEvent.action == "amend",
            )
        )
    )
    assert len(events) == 0


def test_append_only_constraint(
    app_session: Session,
    sample_instrument_data: InstrumentInput,
    actor: LabUser,
) -> None:
    """Instrument is NOT append-only — it should allow UPDATE."""
    instrument = create_instrument(app_session, sample_instrument_data, actor_id=actor.id, actor_role=actor.role)
    updated = update_instrument(app_session, instrument.id, location="Lab D", actor_id=actor.id, actor_role=actor.role)
    assert updated.location == "Lab D"
