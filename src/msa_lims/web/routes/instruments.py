"""Instrument registry endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from msa_lims.db.models import Instrument
from msa_lims.domain.enums import InstrumentStatus, InstrumentType
from msa_lims.instruments import (
    InstrumentInput,
    InstrumentNameConflictError,
    create_instrument,
    get_instrument,
    list_instruments,
    update_instrument,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import INSTRUMENT_CONFLICT, merge_responses

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


class InstrumentOut(BaseModel):
    id: int
    name: str
    instrument_type: str
    manufacturer: str | None
    model: str | None
    serial_number: str | None
    location: str | None
    status: str
    calibration_due_on: date | None

    @classmethod
    def from_model(cls, instrument: Instrument) -> InstrumentOut:
        return cls(
            id=instrument.id,
            name=instrument.name,
            instrument_type=instrument.instrument_type.value,
            manufacturer=instrument.manufacturer,
            model=instrument.model,
            serial_number=instrument.serial_number,
            location=instrument.location,
            status=instrument.status.value,
            calibration_due_on=instrument.calibration_due_on,
        )


class InstrumentCreate(BaseModel):
    name: str
    instrument_type: InstrumentType
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    location: str | None = None
    calibration_due_on: date | None = None


class InstrumentUpdate(BaseModel):
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    location: str | None = None
    calibration_due_on: date | None = None
    status: InstrumentStatus | None = None
    reason: str = "updated"


@router.post(
    "",
    response_model=InstrumentOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(INSTRUMENT_CONFLICT),
)
def create_instrument_endpoint(
    body: InstrumentCreate,
    session: SessionDep,
    actor: ActorDep,
    lab_user: LabUserDep,
) -> InstrumentOut:
    try:
        instrument = create_instrument(
            session,
            InstrumentInput(
                name=body.name,
                instrument_type=body.instrument_type,
                manufacturer=body.manufacturer,
                model=body.model,
                serial_number=body.serial_number,
                location=body.location,
                calibration_due_on=body.calibration_due_on,
            ),
            actor_id=lab_user.id,
            actor_role=actor.role,
        )
    except InstrumentNameConflictError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    session.commit()
    return InstrumentOut.from_model(instrument)


@router.get("", response_model=list[InstrumentOut])
def list_instruments_endpoint(
    session: SessionDep,
    actor: InternalActorDep,
    instrument_type: InstrumentType | None = Query(None),
    status_filter: InstrumentStatus | None = Query(None, alias="status"),
) -> list[InstrumentOut]:
    instruments = list_instruments(
        session,
        instrument_type=instrument_type,
        status=status_filter,
    )
    return [InstrumentOut.from_model(i) for i in instruments]


@router.get("/{instrument_id}", response_model=InstrumentOut)
def read_instrument_endpoint(
    instrument_id: int,
    session: SessionDep,
    actor: InternalActorDep,
) -> InstrumentOut:
    instrument = get_instrument(session, instrument_id)
    return InstrumentOut.from_model(instrument)


@router.patch(
    "/{instrument_id}",
    response_model=InstrumentOut,
    responses=merge_responses(INSTRUMENT_CONFLICT),
)
def update_instrument_endpoint(
    instrument_id: int,
    body: InstrumentUpdate,
    session: SessionDep,
    actor: ActorDep,
    lab_user: LabUserDep,
) -> InstrumentOut:
    try:
        instrument = update_instrument(
            session,
            instrument_id,
            name=body.name,
            manufacturer=body.manufacturer,
            model=body.model,
            serial_number=body.serial_number,
            location=body.location,
            calibration_due_on=body.calibration_due_on,
            status=body.status,
            reason=body.reason,
            actor_id=lab_user.id,
            actor_role=actor.role,
        )
    except InstrumentNameConflictError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    session.commit()
    return InstrumentOut.from_model(instrument)
