"""Flux recipe registration — the reference data crucible charges scale from."""

from __future__ import annotations

from fastapi import APIRouter, status

from msa_lims.flux_recipes.service import FluxRecipeInput, FluxRecipeService
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.schemas import FluxRecipeCreate, FluxRecipeOut

router = APIRouter(prefix="/api", tags=["flux-recipes"])


@router.post("/flux-recipes", response_model=FluxRecipeOut, status_code=status.HTTP_201_CREATED)
def create_flux_recipe(
    body: FluxRecipeCreate, session: SessionDep, actor: ActorDep, registered_by: LabUserDep
) -> FluxRecipeOut:
    service = FluxRecipeService(session)
    recipe = service.create(
        FluxRecipeInput(
            name=body.name,
            matrix_type=body.matrix_type,
            nominal_portion_g=body.nominal_portion_g,
            litharge_g=body.litharge_g,
            soda_ash_g=body.soda_ash_g,
            borax_g=body.borax_g,
            silica_g=body.silica_g,
            flour_g=body.flour_g,
            nitre_g=body.nitre_g,
        ),
        registered_by=registered_by,
        actor_role=actor.role,
    )
    session.commit()
    return FluxRecipeOut.from_model(recipe)
