"""Quality-control material registration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.qc_materials.service import QcMaterialInput, QcMaterialService
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import QcMaterialCreate, QcMaterialOut

router = APIRouter(prefix="/api/qc-materials", tags=["qc-materials"])


@router.post("", response_model=QcMaterialOut, status_code=status.HTTP_201_CREATED)
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
