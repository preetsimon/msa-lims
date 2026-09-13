"""Instrument registry.

An instrument is a mutable reference-data row: its identity is stable, but
its calibration due date, status, and location change in place.  Unlike
``fire_assay_result`` or ``audit_event``, the instrument table is
**not** append-only — the lab updates calibration dates when certificates
renew and moves machines between locations.  Mutations are audit-trailed
the same way ``sample.status`` changes are: every ``UPDATE`` emits an
``AuditEvent`` with before/after snapshots.

The instrument table is the home for:
- Balance sensitivity (microbalance rows) — replaces the per-request guess
- Solution finish detection limits (ICP rows) — replaces the hardcoded
  default in ``solution_finish_grade``
- Contamination tracing (``fire_assay_result.instrument_id`` FK)
- Per-furnace tray geometry (``batch.furnace_id`` FK)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import Instrument
from msa_lims.domain.enums import MAY_CONFIGURE_LAB, InstrumentStatus, InstrumentType, Role
from msa_lims.domain.lifecycle import InsufficientRoleError


class InstrumentNotFoundError(RuntimeError):
    """No instrument with this id exists."""


class InstrumentNameConflictError(RuntimeError):
    """An instrument with this name already exists."""


@dataclass(frozen=True, slots=True)
class InstrumentInput:
    name: str
    instrument_type: InstrumentType
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    location: str | None = None
    calibration_due_on: date | None = None


def get_instrument(session: Session, instrument_id: int) -> Instrument:
    """The row, or a clear refusal."""
    instrument = session.get(Instrument, instrument_id)
    if instrument is None:
        raise InstrumentNotFoundError(f"no instrument with id {instrument_id}")
    return instrument


def list_instruments(
    session: Session,
    *,
    instrument_type: InstrumentType | None = None,
    status: InstrumentStatus | None = None,
) -> list[Instrument]:
    """All instruments, optionally filtered."""
    stmt = select(Instrument).order_by(Instrument.name)
    if instrument_type is not None:
        stmt = stmt.where(Instrument.instrument_type == instrument_type)
    if status is not None:
        stmt = stmt.where(Instrument.status == status)
    return list(session.scalars(stmt))


def create_instrument(
    session: Session, inp: InstrumentInput, *, actor_id: int, actor_role: Role
) -> Instrument:
    """Register a new instrument.  Rejects duplicate names."""
    if actor_role not in MAY_CONFIGURE_LAB:
        raise InsufficientRoleError(
            f"{actor_role.value} may not register an instrument; this needs one of "
            + ", ".join(sorted(role.value for role in MAY_CONFIGURE_LAB))
        )

    existing = session.scalar(
        select(Instrument).where(Instrument.name == inp.name)
    )
    if existing is not None:
        raise InstrumentNameConflictError(
            f"instrument with name {inp.name!r} already exists"
        )

    instrument = Instrument(
        name=inp.name,
        instrument_type=inp.instrument_type,
        manufacturer=inp.manufacturer,
        model=inp.model,
        serial_number=inp.serial_number,
        location=inp.location,
        calibration_due_on=inp.calibration_due_on,
    )
    session.add(instrument)
    session.flush()

    record_audit_event(
        session,
        table_name="instrument",
        record_id=instrument.id,
        action="create",
        actor_id=actor_id,
        after={"name": instrument.name, "instrument_type": instrument.instrument_type.value},
    )

    return instrument


def update_instrument(
    session: Session,
    instrument_id: int,
    *,
    name: str | None = None,
    manufacturer: str | None = None,
    model: str | None = None,
    serial_number: str | None = None,
    location: str | None = None,
    calibration_due_on: date | None = None,
    status: InstrumentStatus | None = None,
    reason: str = "updated",
    actor_id: int | None = None,
    actor_role: Role | None = None,
) -> Instrument:
    """Update an instrument's mutable fields.  Only supplied (non-None) fields
    are updated; the rest stay as-is.

    Raises ``InstrumentNameConflictError`` if the new name is already taken
    by a different instrument.
    """
    if actor_role is not None and actor_role not in MAY_CONFIGURE_LAB:
        raise InsufficientRoleError(
            f"{actor_role.value} may not update an instrument; this needs one of "
            + ", ".join(sorted(role.value for role in MAY_CONFIGURE_LAB))
        )

    instrument = get_instrument(session, instrument_id)

    # Capture before snapshot for audit
    before: dict[str, object] = {}
    changed: dict[str, object] = {}

    if name is not None and name != instrument.name:
        # Check name uniqueness
        existing = session.scalar(
            select(Instrument).where(
                Instrument.name == name, Instrument.id != instrument_id
            )
        )
        if existing is not None:
            raise InstrumentNameConflictError(
                f"instrument with name {name!r} already exists"
            )
        before["name"] = instrument.name
        changed["name"] = name
        instrument.name = name

    if manufacturer is not None and manufacturer != instrument.manufacturer:
        before["manufacturer"] = instrument.manufacturer
        changed["manufacturer"] = manufacturer
        instrument.manufacturer = manufacturer

    if model is not None and model != instrument.model:
        before["model"] = instrument.model
        changed["model"] = model
        instrument.model = model

    if serial_number is not None and serial_number != instrument.serial_number:
        before["serial_number"] = instrument.serial_number
        changed["serial_number"] = serial_number
        instrument.serial_number = serial_number

    if location is not None and location != instrument.location:
        before["location"] = instrument.location
        changed["location"] = location
        instrument.location = location

    if calibration_due_on is not None and calibration_due_on != instrument.calibration_due_on:
        before["calibration_due_on"] = (
            str(instrument.calibration_due_on) if instrument.calibration_due_on else None
        )
        changed["calibration_due_on"] = str(calibration_due_on)
        instrument.calibration_due_on = calibration_due_on

    if status is not None and status != instrument.status:
        before["status"] = instrument.status.value
        changed["status"] = status.value
        instrument.status = status

    if changed:
        session.flush()
        record_audit_event(
            session,
            table_name="instrument",
            record_id=instrument.id,
            action="amend",
            actor_id=actor_id,
            reason=reason,
            before=before or None,
            after=changed,
        )

    return instrument
