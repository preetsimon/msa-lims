"""Flux recipe registration — the reference data crucible charges scale from."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, status

from msa_lims.flux_recipes.service import (
    FluxRecipeInput,
    FluxRecipeService,
    deactivate_flux_recipe,
    get_flux_recipe,
    list_flux_recipes,
    update_flux_recipe,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import FORBIDDEN_403, merge_responses
from msa_lims.web.schemas import FluxRecipeCreate, FluxRecipeOut

router = APIRouter(prefix="/api", tags=["flux-recipes"])


@router.get("/flux-recipes", response_model=list[FluxRecipeOut])
def read_flux_recipes(
    session: SessionDep,
    actor: InternalActorDep,
    active_only: bool = True,
) -> list[FluxRecipeOut]:
    return [
        FluxRecipeOut.from_model(recipe)
        for recipe in list_flux_recipes(session, active_only=active_only)
    ]


@router.get("/flux-recipes/{recipe_id}", response_model=FluxRecipeOut)
def read_flux_recipe(
    recipe_id: int,
    session: SessionDep,
    actor: InternalActorDep,
) -> FluxRecipeOut:
    from fastapi import HTTPException

    try:
        recipe = get_flux_recipe(session, recipe_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FluxRecipeOut.from_model(recipe)


@router.post(
    "/flux-recipes",
    response_model=FluxRecipeOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403),
)
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


@router.patch("/flux-recipes/{recipe_id}", response_model=FluxRecipeOut)
def update_flux_recipe_endpoint(
    recipe_id: int,
    session: SessionDep,
    actor: ActorDep,
    registered_by: LabUserDep,
    name: str | None = None,
    nominal_portion_g: Decimal | None = None,
    litharge_g: Decimal | None = None,
    soda_ash_g: Decimal | None = None,
    borax_g: Decimal | None = None,
    silica_g: Decimal | None = None,
    flour_g: Decimal | None = None,
    nitre_g: Decimal | None = None,
) -> FluxRecipeOut:
    from fastapi import HTTPException

    try:
        recipe = update_flux_recipe(
            session,
            recipe_id,
            name=name,
            nominal_portion_g=nominal_portion_g,
            litharge_g=litharge_g,
            soda_ash_g=soda_ash_g,
            borax_g=borax_g,
            silica_g=silica_g,
            flour_g=flour_g,
            nitre_g=nitre_g,
            actor_id=registered_by.id,
        )
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FluxRecipeOut.from_model(recipe)


@router.delete("/flux-recipes/{recipe_id}")
def deactivate_flux_recipe_endpoint(
    recipe_id: int,
    session: SessionDep,
    actor: ActorDep,
    registered_by: LabUserDep,
) -> dict[str, str]:
    from fastapi import HTTPException

    try:
        deactivate_flux_recipe(session, recipe_id, actor_id=registered_by.id)
        session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"status": "deactivated"}
