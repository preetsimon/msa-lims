"""Flux recipe registration: reference data a crucible charge is scaled from.

Thin, like ``clients/service.py`` — one uniqueness check, one audit event.
The role gate is ``MAY_CONFIGURE_LAB`` rather than ``MAY_MANAGE_ACCOUNTS``:
defining what goes into a furnace is lab process configuration, a different
decision from the client and billing relationships that tier covers, even
though today both name the same two roles.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, FluxRecipe, LabUser
from msa_lims.domain.enums import MAY_CONFIGURE_LAB, MatrixType, Role
from msa_lims.domain.lifecycle import InsufficientRoleError


class FluxRecipeNotFoundError(ValueError):
    """No flux recipe with this id exists.

    Lives here rather than in :mod:`msa_lims.batches.service`, for the same
    reason :class:`~msa_lims.clients.service.ClientNotFoundError` lives in
    ``clients/service.py`` — the module that owns an entity owns the error
    for "this one doesn't exist."
    """


class FluxRecipeValidationError(ValueError):
    """One or more problems with a flux recipe registration, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class FluxRecipeInput:
    name: str
    matrix_type: MatrixType
    nominal_portion_g: Decimal
    litharge_g: Decimal
    soda_ash_g: Decimal
    borax_g: Decimal
    silica_g: Decimal
    flour_g: Decimal
    nitre_g: Decimal


class FluxRecipeService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, data: FluxRecipeInput, *, registered_by: LabUser, actor_role: Role
    ) -> FluxRecipe:
        if actor_role not in MAY_CONFIGURE_LAB:
            raise InsufficientRoleError(
                f"{actor_role.value} may not register a flux recipe; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_CONFIGURE_LAB))
            )

        name = data.name.strip()
        problems: list[str] = []
        if not name:
            problems.append("recipe name cannot be blank")
        elif self._session.scalar(select(FluxRecipe).where(FluxRecipe.name == name)) is not None:
            problems.append(f"a flux recipe named {name!r} already exists")
        if problems:
            raise FluxRecipeValidationError(problems)

        recipe = FluxRecipe(
            name=name,
            matrix_type=data.matrix_type,
            nominal_portion_g=data.nominal_portion_g,
            litharge_g=data.litharge_g,
            soda_ash_g=data.soda_ash_g,
            borax_g=data.borax_g,
            silica_g=data.silica_g,
            flour_g=data.flour_g,
            nitre_g=data.nitre_g,
        )
        self._session.add(recipe)
        self._session.flush()

        self._session.add(
            AuditEvent(
                table_name="flux_recipe",
                record_id=recipe.id,
                action="create",
                after={"name": recipe.name, "matrix_type": recipe.matrix_type.value},
                actor_id=registered_by.id,
            )
        )
        return recipe
