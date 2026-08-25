"""Furnace batching, against a real Postgres session: opening a batch,
charging crucibles, and firing it through its linear status machine."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.batches.service import (
    BatchInput,
    BatchNotFoundError,
    BatchService,
    BatchValidationError,
    CrucibleChargeInput,
    CruciblePartingInput,
    CrucibleValidationError,
    CrucibleWeighingInput,
    get_batch_detail,
)
from msa_lims.db.models import AuditEvent, Client, Crucible, FluxRecipe, LabUser, Sample, Submission
from msa_lims.domain.batch_lifecycle import FurnacePositionError
from msa_lims.domain.enums import (
    BatchStatus,
    CrucibleStatus,
    MatrixType,
    Role,
    SampleStatus,
    SampleType,
)
from msa_lims.domain.flux import FluxCalculationError
from msa_lims.domain.lifecycle import InsufficientRoleError, TransitionNotAllowedError
from msa_lims.flux_recipes.service import FluxRecipeNotFoundError

pytestmark = pytest.mark.integration

FURNACE = {"furnace_rows": 6, "furnace_columns": 6}


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-batch-1",
        email="a-batch@lab.test",
        full_name="A. Nalyst",
        role=Role.ANALYST,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def client_role_user(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-client-batch-1",
        email="c-batch@lab.test",
        full_name="C. Lient",
        role=Role.CLIENT,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def recipe(app_session: Session) -> FluxRecipe:
    recipe = FluxRecipe(
        name="Standard Silicate",
        matrix_type=MatrixType.SILICATE,
        nominal_portion_g=Decimal("30"),
        litharge_g=Decimal("60"),
        soda_ash_g=Decimal("90"),
        borax_g=Decimal("30"),
        silica_g=Decimal("15"),
        flour_g=Decimal("3"),
        nitre_g=Decimal("0"),
    )
    app_session.add(recipe)
    app_session.flush()
    return recipe


def make_sample(session: Session, *, sample_id: str = "MSA-24-SO-00417") -> Sample:
    client = Client(code=f"MSA{sample_id[-2:]}", name=f"MSA Test Mining Co {sample_id}")
    session.add(client)
    session.flush()
    submission = Submission(
        submission_number=f"SUB-2026-{sample_id[-4:]}",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    session.add(submission)
    session.flush()
    sample = Sample(
        sample_id=sample_id,
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        status=SampleStatus.RECEIVED,
    )
    session.add(sample)
    session.flush()
    return sample


@pytest.fixture
def a_sample(app_session: Session) -> Sample:
    return make_sample(app_session)


def charge_input(**overrides: object) -> CrucibleChargeInput:
    defaults: dict[str, object] = {
        "batch_id": 1,
        "sample_id": 1,
        "flux_recipe_id": 1,
        "position_row": 1,
        "position_col": 1,
        "sample_weight_g": Decimal("30"),
        "charged_at": datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
        "notes": None,
    }
    defaults.update(overrides)
    return CrucibleChargeInput(**defaults)  # type: ignore[arg-type]


class TestOpeningABatch:
    def test_a_batch_is_opened_pending(self, app_session: Session, analyst: LabUser) -> None:
        service = BatchService(app_session, **FURNACE)
        batch = service.create_batch(
            BatchInput(opened_at=datetime(2026, 8, 25, tzinfo=UTC)),
            opened_by=analyst,
            actor_role=Role.ANALYST,
        )
        assert batch.status is BatchStatus.PENDING
        assert batch.batch_number.startswith("BATCH-2026-")

    def test_sequential_batches_get_sequential_numbers(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        first = service.create_batch(
            BatchInput(opened_at=datetime(2026, 8, 25, tzinfo=UTC)),
            opened_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()
        second = service.create_batch(
            BatchInput(opened_at=datetime(2026, 8, 25, tzinfo=UTC)),
            opened_by=analyst,
            actor_role=Role.ANALYST,
        )
        assert second.batch_number != first.batch_number

    def test_a_client_role_may_not_open_a_batch(
        self, app_session: Session, client_role_user: LabUser
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(InsufficientRoleError):
            service.create_batch(
                BatchInput(opened_at=datetime(2026, 8, 25, tzinfo=UTC)),
                opened_by=client_role_user,
                actor_role=Role.CLIENT,
            )


@pytest.fixture
def charging_batch(app_session: Session, analyst: LabUser):  # type: ignore[no-untyped-def]
    """A batch already advanced to CHARGING -- the state crucible charging needs."""
    service = BatchService(app_session, **FURNACE)
    batch = service.create_batch(
        BatchInput(opened_at=datetime(2026, 8, 25, tzinfo=UTC)),
        opened_by=analyst,
        actor_role=Role.ANALYST,
    )
    app_session.flush()
    service.advance_status(
        batch.id, target=BatchStatus.CHARGING, advanced_by=analyst, actor_role=Role.ANALYST
    )
    app_session.flush()
    return batch


@pytest.fixture
def prep_tech(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-prep-batch-1",
        email="prep-batch@lab.test",
        full_name="P. Rep Tech",
        role=Role.PREP_TECH,
    )
    app_session.add(user)
    app_session.flush()
    return user


def cupelled_crucible(
    app_session: Session,
    analyst: LabUser,
    charging_batch,  # type: ignore[no-untyped-def]
    recipe: FluxRecipe,
    a_sample: Sample,
) -> Crucible:
    """A charged crucible whose batch has been walked to CUPELLED."""
    service = BatchService(app_session, **FURNACE)
    crucible = service.charge_crucible(
        charge_input(batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id),
        charged_by=analyst,
        actor_role=Role.ANALYST,
    )
    for stage in (
        BatchStatus.IN_FUSION,
        BatchStatus.FUSED,
        BatchStatus.IN_CUPELLATION,
        BatchStatus.CUPELLED,
    ):
        service.advance_status(
            charging_batch.id, target=stage, advanced_by=analyst, actor_role=Role.ANALYST
        )
    app_session.flush()
    return crucible


class TestRecordingParting:
    def test_parting_records_the_measurements_and_advances_the_crucible(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        crucible = cupelled_crucible(app_session, analyst, charging_batch, recipe, a_sample)
        service = BatchService(app_session, **FURNACE)
        parted = service.record_parting(
            charging_batch.id,
            crucible.id,
            CruciblePartingInput(
                lead_button_weight_mg=Decimal("27.8"),
                prill_weight_mg=Decimal("0.512"),
                parting_acid_volume_ml=Decimal("5"),
                parted_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
            ),
            parted_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert parted.status is CrucibleStatus.PARTED
        assert parted.lead_button_weight_mg == Decimal("27.8")
        assert parted.prill_weight_mg == Decimal("0.512")
        assert parted.parting_acid_volume_ml == Decimal("5")
        assert parted.parted_at == datetime(2026, 8, 25, 10, 0, tzinfo=UTC)

    def test_parting_before_cupellation_is_refused(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        crucible = service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        with pytest.raises(TransitionNotAllowedError):
            service.record_parting(
                charging_batch.id,
                crucible.id,
                parting_input(),
                parted_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_parting_a_crucible_still_in_fusion_is_refused(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        crucible = service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        for stage in (BatchStatus.IN_FUSION, BatchStatus.FUSED):
            service.advance_status(
                charging_batch.id, target=stage, advanced_by=analyst, actor_role=Role.ANALYST
            )
        assert crucible.status is CrucibleStatus.FUSED
        with pytest.raises(TransitionNotAllowedError, match="fused to parted"):
            service.record_parting(
                charging_batch.id,
                crucible.id,
                parting_input(),
                parted_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_parting_twice_is_refused(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        crucible = cupelled_crucible(app_session, analyst, charging_batch, recipe, a_sample)
        service = BatchService(app_session, **FURNACE)
        service.record_parting(
            charging_batch.id,
            crucible.id,
            parting_input(),
            parted_by=analyst,
            actor_role=Role.ANALYST,
        )
        with pytest.raises(TransitionNotAllowedError, match="already parted"):
            service.record_parting(
                charging_batch.id,
                crucible.id,
                parting_input(),
                parted_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_prep_tech_may_record_parting_but_a_client_may_not(
        self,
        app_session: Session,
        analyst: LabUser,
        client_role_user: LabUser,
        prep_tech: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        """Parting is bench work -- the same tier as charging -- not result
        interpretation; the external role has no place at the bench at all."""
        crucible = cupelled_crucible(app_session, analyst, charging_batch, recipe, a_sample)
        service = BatchService(app_session, **FURNACE)

        parted = service.record_parting(
            charging_batch.id,
            crucible.id,
            parting_input(),
            parted_by=prep_tech,
            actor_role=Role.PREP_TECH,
        )
        assert parted.status is CrucibleStatus.PARTED

        app_session.refresh(crucible)
        crucible.status = CrucibleStatus.CUPELLED
        app_session.flush()
        with pytest.raises(InsufficientRoleError):
            service.record_parting(
                charging_batch.id,
                crucible.id,
                parting_input(),
                parted_by=client_role_user,
                actor_role=Role.CLIENT,
            )

    def test_parting_writes_a_transition_audit_event_with_the_measurements(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        crucible = cupelled_crucible(app_session, analyst, charging_batch, recipe, a_sample)
        service = BatchService(app_session, **FURNACE)
        parted = service.record_parting(
            charging_batch.id,
            crucible.id,
            parting_input(),
            parted_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.table_name == "crucible",
                AuditEvent.record_id == parted.id,
                AuditEvent.action == "transition",
            )
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        assert event.action == "transition"
        assert event.before == {"status": "cupelled"}
        assert event.after["status"] == "parted"
        assert event.after["prill_weight_mg"] == "0.512"

    def test_parting_a_crucible_from_another_batch_is_refused(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        from msa_lims.fire_assay_results.service import CrucibleNotFoundError

        crucible = cupelled_crucible(app_session, analyst, charging_batch, recipe, a_sample)
        other = BatchService(app_session, **FURNACE).create_batch(
            BatchInput(opened_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC)),
            opened_by=analyst,
            actor_role=Role.ANALYST,
        )
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(CrucibleNotFoundError):
            service.record_parting(
                other.id, crucible.id, parting_input(), parted_by=analyst, actor_role=Role.ANALYST
            )


class TestRecordingWeighing:
    def test_weighing_records_the_bead_and_advances_to_weighed(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        crucible = cupelled_crucible(app_session, analyst, charging_batch, recipe, a_sample)
        service = BatchService(app_session, **FURNACE)
        service.record_parting(
            charging_batch.id,
            crucible.id,
            parting_input(),
            parted_by=analyst,
            actor_role=Role.ANALYST,
        )
        weighed = service.record_weighing(
            charging_batch.id,
            crucible.id,
            CrucibleWeighingInput(
                gold_bead_mg=Decimal("0.225"), weighed_at=datetime(2026, 8, 25, 11, 0, tzinfo=UTC)
            ),
            weighed_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert weighed.status is CrucibleStatus.WEIGHED
        assert weighed.gold_bead_mg == Decimal("0.225")
        assert weighed.weighed_at == datetime(2026, 8, 25, 11, 0, tzinfo=UTC)

    def test_weighing_before_parting_is_refused(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,  # type: ignore[no-untyped-def]
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        crucible = cupelled_crucible(app_session, analyst, charging_batch, recipe, a_sample)
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(TransitionNotAllowedError, match="cupelled to weighed"):
            service.record_weighing(
                charging_batch.id,
                crucible.id,
                weighing_input(),
                weighed_by=analyst,
                actor_role=Role.ANALYST,
            )


def parting_input(**overrides: object) -> CruciblePartingInput:
    defaults: dict[str, object] = {
        "lead_button_weight_mg": Decimal("27.8"),
        "prill_weight_mg": Decimal("0.512"),
        "parting_acid_volume_ml": Decimal("5"),
        "parted_at": datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return CruciblePartingInput(**defaults)  # type: ignore[arg-type]


def weighing_input(**overrides: object) -> CrucibleWeighingInput:
    defaults: dict[str, object] = {
        "gold_bead_mg": Decimal("0.225"),
        "weighed_at": datetime(2026, 8, 25, 11, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return CrucibleWeighingInput(**defaults)  # type: ignore[arg-type]


class TestChargingACrucible:
    def test_a_crucible_is_charged_and_flux_scaled(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        crucible = service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id,
                sample_id=a_sample.id,
                flux_recipe_id=recipe.id,
                sample_weight_g=Decimal("60"),  # double the nominal 30 g
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        assert crucible.status is CrucibleStatus.CHARGED
        assert crucible.litharge_g == Decimal("120")  # doubled from the 60 g recipe
        assert crucible.soda_ash_g == Decimal("180")

    def test_charging_moves_the_sample_to_in_assay(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        assert a_sample.status is SampleStatus.IN_ASSAY

    def test_charging_into_a_pending_batch_is_refused(
        self, app_session: Session, analyst: LabUser, recipe: FluxRecipe, a_sample: Sample
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        pending_batch = service.create_batch(
            BatchInput(opened_at=datetime(2026, 8, 25, tzinfo=UTC)),
            opened_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(CrucibleValidationError, match="not charging"):
            service.charge_crucible(
                charge_input(
                    batch_id=pending_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
                ),
                charged_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_sample_already_in_assay_cannot_be_charged_again(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id,
                sample_id=a_sample.id,
                flux_recipe_id=recipe.id,
                position_row=1,
                position_col=1,
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(CrucibleValidationError, match="cannot be charged"):
            service.charge_crucible(
                charge_input(
                    batch_id=charging_batch.id,
                    sample_id=a_sample.id,
                    flux_recipe_id=recipe.id,
                    position_row=1,
                    position_col=2,
                ),
                charged_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_position_already_occupied_is_refused(
        self, app_session: Session, analyst: LabUser, charging_batch, recipe: FluxRecipe
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        first_sample = make_sample(app_session, sample_id="MSA-24-SO-00001")
        second_sample = make_sample(app_session, sample_id="MSA-24-SO-00002")
        service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id,
                sample_id=first_sample.id,
                flux_recipe_id=recipe.id,
                position_row=2,
                position_col=3,
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(CrucibleValidationError, match="already occupied"):
            service.charge_crucible(
                charge_input(
                    batch_id=charging_batch.id,
                    sample_id=second_sample.id,
                    flux_recipe_id=recipe.id,
                    position_row=2,
                    position_col=3,
                ),
                charged_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_position_outside_the_tray_is_refused(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(FurnacePositionError):
            service.charge_crucible(
                charge_input(
                    batch_id=charging_batch.id,
                    sample_id=a_sample.id,
                    flux_recipe_id=recipe.id,
                    position_row=7,
                    position_col=1,
                ),
                charged_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_an_unknown_flux_recipe_is_refused(
        self, app_session: Session, analyst: LabUser, charging_batch, a_sample: Sample
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(FluxRecipeNotFoundError):
            service.charge_crucible(
                charge_input(
                    batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=999_999
                ),
                charged_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_an_unknown_batch_is_refused(
        self, app_session: Session, analyst: LabUser, recipe: FluxRecipe, a_sample: Sample
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(BatchNotFoundError):
            service.charge_crucible(
                charge_input(batch_id=999_999, sample_id=a_sample.id, flux_recipe_id=recipe.id),
                charged_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_zero_sample_weight_raises_the_domain_flux_error(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        with pytest.raises(FluxCalculationError):
            BatchService(app_session, **FURNACE).charge_crucible(
                charge_input(
                    batch_id=charging_batch.id,
                    sample_id=a_sample.id,
                    flux_recipe_id=recipe.id,
                    sample_weight_g=Decimal("0"),
                ),
                charged_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_charging_writes_an_audit_event(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        crucible = service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "crucible", AuditEvent.record_id == crucible.id
            )
        )
        assert event is not None
        assert event.action == "create"


class TestAdvancingBatchStatus:
    def test_the_full_linear_walk(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        for target in (
            BatchStatus.IN_FUSION,
            BatchStatus.FUSED,
            BatchStatus.IN_CUPELLATION,
            BatchStatus.CUPELLED,
            BatchStatus.COMPLETED,
        ):
            batch = service.advance_status(
                charging_batch.id, target=target, advanced_by=analyst, actor_role=Role.ANALYST
            )
            assert batch.status is target

    def test_fusing_bulk_advances_crucible_status(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        crucible = service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        service.advance_status(
            charging_batch.id,
            target=BatchStatus.IN_FUSION,
            advanced_by=analyst,
            actor_role=Role.ANALYST,
        )
        service.advance_status(
            charging_batch.id,
            target=BatchStatus.FUSED,
            advanced_by=analyst,
            actor_role=Role.ANALYST,
        )
        assert crucible.status is CrucibleStatus.FUSED

    def test_firing_an_empty_batch_is_refused(
        self, app_session: Session, analyst: LabUser, charging_batch
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(BatchValidationError, match="no charged crucibles"):
            service.advance_status(
                charging_batch.id,
                target=BatchStatus.IN_FUSION,
                advanced_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_skipping_a_stage_is_refused(
        self, app_session: Session, analyst: LabUser, charging_batch
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(TransitionNotAllowedError):
            service.advance_status(
                charging_batch.id,
                target=BatchStatus.FUSED,
                advanced_by=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_client_role_may_not_advance_a_batch(
        self, app_session: Session, analyst: LabUser, client_role_user: LabUser, charging_batch
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(InsufficientRoleError):
            service.advance_status(
                charging_batch.id,
                target=BatchStatus.IN_FUSION,
                advanced_by=client_role_user,
                actor_role=Role.CLIENT,
            )

    def test_an_unknown_batch_is_refused(self, app_session: Session, analyst: LabUser) -> None:
        service = BatchService(app_session, **FURNACE)
        with pytest.raises(BatchNotFoundError):
            service.advance_status(
                999_999, target=BatchStatus.CHARGING, advanced_by=analyst, actor_role=Role.ANALYST
            )

    def test_advancing_writes_a_transition_audit_event(
        self,
        app_session: Session,
        analyst: LabUser,
        charging_batch,
        recipe: FluxRecipe,
        a_sample: Sample,
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id, sample_id=a_sample.id, flux_recipe_id=recipe.id
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()
        service.advance_status(
            charging_batch.id,
            target=BatchStatus.IN_FUSION,
            advanced_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.table_name == "batch",
                AuditEvent.record_id == charging_batch.id,
                AuditEvent.action == "transition",
            )
            .order_by(AuditEvent.id.desc())
        )
        assert event is not None
        assert event.after == {"status": "in_fusion"}


class TestBatchDetail:
    def test_crucibles_are_ordered_by_position(
        self, app_session: Session, analyst: LabUser, charging_batch, recipe: FluxRecipe
    ) -> None:
        service = BatchService(app_session, **FURNACE)
        sample_a = make_sample(app_session, sample_id="MSA-24-SO-00011")
        sample_b = make_sample(app_session, sample_id="MSA-24-SO-00012")
        service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id,
                sample_id=sample_a.id,
                flux_recipe_id=recipe.id,
                position_row=2,
                position_col=1,
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        service.charge_crucible(
            charge_input(
                batch_id=charging_batch.id,
                sample_id=sample_b.id,
                flux_recipe_id=recipe.id,
                position_row=1,
                position_col=1,
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        detail = get_batch_detail(app_session, charging_batch.id)
        assert [c.position_row for c in detail.crucibles] == [1, 2]

    def test_an_unknown_batch_is_refused(self, app_session: Session) -> None:
        with pytest.raises(BatchNotFoundError):
            get_batch_detail(app_session, 999_999)
