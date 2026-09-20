"""Quality-control material registration endpoints."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, status

from msa_lims.qc_materials.service import (
    QcMaterialInput,
    QcMaterialService,
    deactivate_qc_material,
    get_qc_material,
    list_qc_materials,
    update_qc_material,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import FORBIDDEN_403, merge_responses
from msa_lims.web.schemas import QcMaterialCreate, QcMaterialOut

router = APIRouter(prefix="/api/qc-materials", tags=["qc-materials"])


@router.get("", response_model=list[QcMaterialOut])
def read_qc_materials(
    session: SessionDep,
    actor: InternalActorDep,
    active_only: bool = True,
) -> list[QcMaterialOut]:
    return [
        QcMaterialOut.from_model(material)
        for material in list_qc_materials(session, active_only=active_only)
    ]


@router.get("/{material_id}", response_model=QcMaterialOut)
def read_qc_material(
    material_id: int,
    session: SessionDep,
    actor: InternalActorDep,
) -> QcMaterialOut:
    from fastapi import HTTPException

    try:
        material = get_qc_material(session, material_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return QcMaterialOut.from_model(material)


@router.post(
    "",
    response_model=QcMaterialOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403),
)
def create_qc_material(
    body: QcMaterialCreate, session: SessionDep, actor: ActorDep, registered_by: LabUserDep
) -> QcMaterialOut:
    service = QcMaterialService(session)
    material = service.create(
        QcMaterialInput(
            name=body.name,
            qc_type=body.qc_type,
            lot_number=body.lot_number,
            certified_au_value_g_t=body.certified_au_value_g_t,
            certified_au_uncertainty_g_t=body.certified_au_uncertainty_g_t,
            notes=body.notes,
        ),
        registered_by=registered_by,
        actor_role=actor.role,
    )
    session.commit()
    return QcMaterialOut.from_model(material)


@router.patch("/{material_id}", response_model=QcMaterialOut)
def update_qc_material_endpoint(
    material_id: int,
    session: SessionDep,
    actor: ActorDep,
    registered_by: LabUserDep,
    name: str | None = None,
    lot_number: str | None = None,
    certified_au_value_g_t: Decimal | None = None,
    certified_au_uncertainty_g_t: Decimal | None = None,
    notes: str | None = None,
) -> QcMaterialOut:
    from fastapi import HTTPException

    try:
        material = update_qc_material(
            session,
            material_id,
            name=name,
            lot_number=lot_number,
            certified_au_value_g_t=certified_au_value_g_t,
            certified_au_uncertainty_g_t=certified_au_uncertainty_g_t,
            notes=notes,
            actor_id=registered_by.id,
        )
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return QcMaterialOut.from_model(material)


@router.delete("/{material_id}")
def deactivate_qc_material_endpoint(
    material_id: int,
    session: SessionDep,
    actor: ActorDep,
    registered_by: LabUserDep,
) -> dict[str, str]:
    from fastapi import HTTPException

    try:
        deactivate_qc_material(session, material_id, actor_id=registered_by.id)
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "deactivated"}
