"""Flux recipe registration, against a real Postgres session."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, LabUser
from msa_lims.domain.enums import MatrixType, Role
from msa_lims.domain.lifecycle import InsufficientRoleError
from msa_lims.flux_recipes.service import (
    FluxRecipeInput,
    FluxRecipeService,
    FluxRecipeValidationError,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def supervisor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-supervisor-fr-1",
        email="sup-fr@lab.test",
        full_name="S. Upervisor",
        role=Role.SUPERVISOR,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-fr-1", email="a-fr@lab.test", full_name="A. Nalyst", role=Role.ANALYST
    )
    app_session.add(user)
    app_session.flush()
    return user


def recipe_input(**overrides: object) -> FluxRecipeInput:
    defaults: dict[str, object] = {
        "name": "Standard Silicate",
        "matrix_type": MatrixType.SILICATE,
        "nominal_portion_g": Decimal("30"),
        "litharge_g": Decimal("60"),
        "soda_ash_g": Decimal("90"),
        "borax_g": Decimal("30"),
        "silica_g": Decimal("15"),
        "flour_g": Decimal("3"),
        "nitre_g": Decimal("0"),
    }
    defaults.update(overrides)
    return FluxRecipeInput(**defaults)  # type: ignore[arg-type]


class TestRegisteringARecipe:
    def test_a_supervisor_registers_a_recipe(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        service = FluxRecipeService(app_session)
        recipe = service.create(
            recipe_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR
        )
        app_session.flush()

        assert recipe.name == "Standard Silicate"
        assert recipe.matrix_type is MatrixType.SILICATE
        assert recipe.is_active is True

    def test_an_analyst_may_not_register_a_recipe(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        service = FluxRecipeService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create(recipe_input(), registered_by=analyst, actor_role=Role.ANALYST)

    def test_a_duplicate_name_is_refused(self, app_session: Session, supervisor: LabUser) -> None:
        service = FluxRecipeService(app_session)
        service.create(recipe_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR)
        app_session.flush()

        with pytest.raises(FluxRecipeValidationError, match="already exists"):
            service.create(recipe_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR)

    def test_a_blank_name_is_refused(self, app_session: Session, supervisor: LabUser) -> None:
        service = FluxRecipeService(app_session)
        with pytest.raises(FluxRecipeValidationError, match="blank"):
            service.create(
                recipe_input(name="   "), registered_by=supervisor, actor_role=Role.SUPERVISOR
            )

    def test_registering_a_recipe_writes_an_audit_event(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        service = FluxRecipeService(app_session)
        recipe = service.create(
            recipe_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "flux_recipe", AuditEvent.record_id == recipe.id
            )
        )
        assert event is not None
        assert event.action == "create"
        assert event.actor_id == supervisor.id
