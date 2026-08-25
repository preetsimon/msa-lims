"""Drill hole registration — the last reference-data gap before submission
intake can run entirely through HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.drill_holes.service import DrillHoleInput, DrillHoleService
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import DrillHoleCreate, DrillHoleOut

router = APIRouter(prefix="/api", tags=["drill-holes"])


@router.post("/drill-holes", response_model=DrillHoleOut, status_code=status.HTTP_201_CREATED)
def create_drill_hole(
    body: DrillHoleCreate, session: SessionDep, actor: ActorDep, registered_by: LabUserDep
) -> DrillHoleOut:
    service = DrillHoleService(session)
    hole = service.create(
        DrillHoleInput(
            project_id=body.project_id,
            hole_id=body.hole_id,
            easting=body.easting,
            northing=body.northing,
            elevation_m=body.elevation_m,
            utm_zone=body.utm_zone,
            total_depth_m=body.total_depth_m,
            dip_degrees=body.dip_degrees,
            azimuth_degrees=body.azimuth_degrees,
            drilling_method=body.drilling_method,
        ),
        registered_by=registered_by,
        actor_role=actor.role,
    )
    session.commit()
    return DrillHoleOut.from_model(hole)
