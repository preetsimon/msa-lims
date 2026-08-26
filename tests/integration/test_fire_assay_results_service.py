"""Fire assay result entry, against a real Postgres session.

The two properties that matter most: results are genuinely append-only (never
UPDATE, never DELETE — proven against the restricted role directly, matching
``test_append_only.py``'s pattern), and a supersession chain cannot branch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, Client, Crucible, LabUser, Sample, Submission
from msa_lims.domain.enums import AssayMethod, CrucibleStatus, Role, SampleStatus, SampleType
from msa_lims.domain.lifecycle import InsufficientRoleError, TransitionNotAllowedError
from msa_lims.domain.units import Unit
from msa_lims.fire_assay_results.service import (
    FireAssayResultInput,
    FireAssayResultService,
    FireAssayResultValidationError,
    SampleNotFoundError,
    SolutionFinishInput,
    current_result,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-far-1",
        email="far1@lab.test",
        full_name="A. Nalyst",
        role=Role.ANALYST,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def supervisor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-supervisor-far-1",
        email="sup1@lab.test",
        full_name="S. Upervisor",
        role=Role.SUPERVISOR,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def a_sample(app_session: Session) -> Sample:
    client = Client(code="MSA", name="MSA Test Mining Co")
    app_session.add(client)
    app_session.flush()

    submission = Submission(
        submission_number="SUB-2026-9001",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    app_session.add(submission)
    app_session.flush()

    sample = Sample(
        sample_id="MSA-24-SO-00417",
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        # Entering a result now requires the real IN_ASSAY status (see
        # fire_assay_results/service.py's module docstring) — set up
        # directly since this fixture is testing result entry, not charging.
        status=SampleStatus.IN_ASSAY,
    )
    app_session.add(sample)
    app_session.flush()
    return sample


def result_input(**overrides: object) -> FireAssayResultInput:
    defaults: dict[str, object] = {
        "sample_id": 1,
        "gold_bead_mg": Decimal("0.150"),
        "sample_weight_g": Decimal("30"),
        "balance_sensitivity_mg": None,
        "analysed_at": datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
        "notes": None,
        "supersedes_id": None,
        "superseded_reason": None,
    }
    defaults.update(overrides)
    return FireAssayResultInput(**defaults)  # type: ignore[arg-type]


class TestEnteringAResult:
    def test_the_grade_is_computed_from_the_weighing(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        # 0.150 mg of gold from 30 g of rock is 5 g/t.
        assert result.au_value == Decimal("5")
        assert result.au_censored is False
        assert result.au_unit == "g/t"
        assert result.method.value == "fire_assay_gravimetric"

    def test_entering_a_result_moves_the_sample_to_assayed(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()
        assert a_sample.status is SampleStatus.ASSAYED

    def test_a_bead_below_sensitivity_is_stored_as_a_non_detect(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(
                sample_id=a_sample.id,
                gold_bead_mg=Decimal("0.0005"),
                balance_sensitivity_mg=Decimal("0.001"),
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        assert result.au_censored is True
        assert result.au_value is None
        assert result.au_detection_limit is not None

    def test_a_prep_tech_may_not_enter_a_result(
        self, app_session: Session, a_sample: Sample
    ) -> None:
        prep_tech = LabUser(
            subject="sub-prep-far-1", email="p1@lab.test", full_name="P. Rep", role=Role.PREP_TECH
        )
        app_session.add(prep_tech)
        app_session.flush()

        service = FireAssayResultService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create(
                result_input(sample_id=a_sample.id), analyst=prep_tech, actor_role=Role.PREP_TECH
            )

    def test_an_unknown_sample_is_refused(self, app_session: Session, analyst: LabUser) -> None:
        service = FireAssayResultService(app_session)
        with pytest.raises(SampleNotFoundError):
            service.create(
                result_input(sample_id=999_999), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_rejected_sample_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        a_sample.status = SampleStatus.REJECTED
        app_session.flush()

        service = FireAssayResultService(app_session)
        with pytest.raises(TransitionNotAllowedError):
            service.create(
                result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_sample_not_yet_in_assay_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """The real transition, not the old any-non-rejected-status bypass:
        a sample still RECEIVED (never charged into a crucible) cannot have
        a result entered against it."""
        a_sample.status = SampleStatus.RECEIVED
        app_session.flush()

        service = FireAssayResultService(app_session)
        with pytest.raises(TransitionNotAllowedError):
            service.create(
                result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_second_new_result_against_an_already_resulted_sample_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="already has a result"):
            service.create(
                result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_negative_weight_from_domain_assay_surfaces_as_the_domain_error(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        from msa_lims.domain.assay import AssayCalculationError

        service = FireAssayResultService(app_session)
        with pytest.raises(AssayCalculationError):
            service.create(
                result_input(sample_id=a_sample.id, sample_weight_g=Decimal("0")),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_entering_a_result_writes_an_audit_event(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        events = app_session.scalars(
            select(AuditEvent).where(
                AuditEvent.table_name == "fire_assay_result", AuditEvent.record_id == result.id
            )
        ).all()
        assert len(events) == 1
        assert events[0].action == "create"
        assert events[0].actor_id == analyst.id


class TestSupersession:
    def test_a_supervisor_corrects_a_result(
        self, app_session: Session, analyst: LabUser, supervisor: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        second = service.create(
            result_input(
                sample_id=a_sample.id,
                gold_bead_mg=Decimal("0.160"),
                supersedes_id=first.id,
                superseded_reason="transcription error at the balance",
            ),
            analyst=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()

        assert second.supersedes_id == first.id
        assert second.au_value != first.au_value

    def test_superseding_writes_an_amend_audit_event_with_the_reason(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        second = service.create(
            result_input(
                sample_id=a_sample.id,
                supersedes_id=first.id,
                superseded_reason="re-weighed after balance drift found",
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "fire_assay_result", AuditEvent.record_id == second.id
            )
        )
        assert event is not None
        assert event.action == "amend"
        assert event.reason == "re-weighed after balance drift found"

    def test_superseding_without_a_reason_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="requires a reason"):
            service.create(
                result_input(sample_id=a_sample.id, supersedes_id=first.id),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_superseding_a_row_that_is_not_current_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """Prevents a branching chain: only the head can be corrected."""
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()
        service.create(
            result_input(
                sample_id=a_sample.id,
                supersedes_id=first.id,
                superseded_reason="first correction",
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="only the current result"):
            service.create(
                result_input(
                    sample_id=a_sample.id,
                    supersedes_id=first.id,  # already superseded once
                    superseded_reason="second attempt at correcting the original",
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_superseding_a_result_from_another_sample_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        other_submission_client = Client(code="OTH", name="Other Mining Co")
        app_session.add(other_submission_client)
        app_session.flush()
        other_submission = Submission(
            submission_number="SUB-2026-9002",
            client_id=other_submission_client.id,
            received_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
        app_session.add(other_submission)
        app_session.flush()
        other_sample = Sample(
            sample_id="MSA-24-SO-00418",
            submission_id=other_submission.id,
            sample_type=SampleType.SOIL,
            status=SampleStatus.RECEIVED,
        )
        app_session.add(other_sample)
        app_session.flush()

        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        with pytest.raises(FireAssayResultValidationError, match="not the current result"):
            service.create(
                result_input(
                    sample_id=other_sample.id,
                    supersedes_id=first.id,
                    superseded_reason="wrong sample entirely",
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_superseding_does_not_touch_sample_status(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()
        assert a_sample.status is SampleStatus.ASSAYED

        service.create(
            result_input(
                sample_id=a_sample.id, supersedes_id=first.id, superseded_reason="a correction"
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        assert a_sample.status is SampleStatus.ASSAYED


def solution_input(**overrides: object) -> SolutionFinishInput:
    defaults: dict[str, object] = {
        "sample_id": 1,
        "method": AssayMethod.FIRE_ASSAY_AAS,
        "concentration": Decimal("15"),
        "concentration_unit": Unit.MG_PER_L,
        "solution_volume_ml": Decimal("10"),
        "sample_weight_g": Decimal("30"),
        "analysed_at": datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
        "detection_limit": None,
        "upper_calibration_limit": None,
        "notes": None,
        "supersedes_id": None,
        "superseded_reason": None,
        "crucible_id": None,
    }
    defaults.update(overrides)
    return SolutionFinishInput(**defaults)  # type: ignore[arg-type]


class TestSolutionFinish:
    """The AAS/ICP-MS entry point: a concentration, not a bead weight, but
    the same sample admission and the same supersession chain — see the
    module docstring for why the two finishes share both."""

    def test_the_grade_is_computed_from_the_concentration(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create_solution_finish(
            solution_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        # 15 mg/L in a 10 mL flask, from a 30 g portion, is 5 g/t — the same
        # answer test_the_grade_is_computed_from_the_weighing gets from the
        # equivalent bead, because it is the same physical gold either way.
        assert result.au_value == Decimal("5")
        assert result.method.value == "fire_assay_aas"
        assert result.gold_bead_mg is None
        assert result.solution_concentration == Decimal("15")

    def test_entering_a_solution_result_moves_the_sample_to_assayed(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        service.create_solution_finish(
            solution_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()
        assert a_sample.status is SampleStatus.ASSAYED

    def test_a_sample_not_yet_in_assay_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """The same admission gate as the gravimetric path — a solution
        finish does not get its own, looser rule about sample status."""
        a_sample.status = SampleStatus.RECEIVED
        app_session.flush()

        service = FireAssayResultService(app_session)
        with pytest.raises(TransitionNotAllowedError):
            service.create_solution_finish(
                solution_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
            )

    def test_a_prep_tech_may_not_enter_a_solution_result(
        self, app_session: Session, a_sample: Sample
    ) -> None:
        prep_tech = LabUser(
            subject="sub-prep-far-2", email="p2@lab.test", full_name="P. Rep", role=Role.PREP_TECH
        )
        app_session.add(prep_tech)
        app_session.flush()

        service = FireAssayResultService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.create_solution_finish(
                solution_input(sample_id=a_sample.id),
                analyst=prep_tech,
                actor_role=Role.PREP_TECH,
            )

    def test_the_gravimetric_method_is_refused_at_this_entry_point(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """Reachable only by calling the service directly — the route's own
        schema does not offer this method — but refused here too, because the
        row it would build (a concentration with no bead) violates the
        gravimetric CHECK constraint regardless of which code path built it."""
        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="weighs a bead"):
            service.create_solution_finish(
                solution_input(sample_id=a_sample.id, method=AssayMethod.FIRE_ASSAY_GRAVIMETRIC),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_reading_above_the_calibration_range_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        from msa_lims.domain.assay import AssayCalculationError

        service = FireAssayResultService(app_session)
        with pytest.raises(AssayCalculationError, match="calibration range"):
            service.create_solution_finish(
                solution_input(
                    sample_id=a_sample.id,
                    concentration=Decimal("400"),
                    upper_calibration_limit=Decimal("300"),
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_result_with_neither_a_crucible_nor_a_weight_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        with pytest.raises(
            FireAssayResultValidationError, match="sample_weight_g is required unless"
        ):
            service.create_solution_finish(
                solution_input(sample_id=a_sample.id, sample_weight_g=None),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_named_crucible_derives_the_portion_but_not_a_bead(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        """Unlike the gravimetric path, a solution finish never reads a
        crucible's recorded bead back — the bead it dissolved is exactly the
        one this request's own concentration accounts for."""
        sample, crucible = CupelledChain.make(app_session, analyst)
        service = FireAssayResultService(app_session)
        result = service.create_solution_finish(
            solution_input(sample_id=sample.id, sample_weight_g=None, crucible_id=crucible.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        # The crucible's own recorded charge is 45 g (see CupelledChain); 15
        # mg/L in 10 mL from that 45 g portion is 10/3 g/t.
        assert result.sample_weight_g == Decimal("45")
        assert result.crucible_id == crucible.id
        assert result.gold_bead_mg is None

    def test_a_supervisor_corrects_a_solution_result_with_another_solution_result(
        self, app_session: Session, analyst: LabUser, supervisor: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create_solution_finish(
            solution_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        second = service.create_solution_finish(
            solution_input(
                sample_id=a_sample.id,
                concentration=Decimal("16.5"),
                supersedes_id=first.id,
                superseded_reason="re-diluted after a pipetting error",
            ),
            analyst=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()

        assert second.supersedes_id == first.id
        assert second.au_value != first.au_value

    def test_a_saturated_solution_result_is_superseded_by_a_gravimetric_re_assay(
        self, app_session: Session, analyst: LabUser, supervisor: LabUser, a_sample: Sample
    ) -> None:
        """The scenario the shared chain exists for: an AAS screen comes back
        pinned to the top of its curve, so the lab re-runs the sample
        gravimetrically — the referee method, which has no ceiling — and that
        second result corrects the first. One chain answers "what is this
        sample's grade" throughout, across both finishes."""
        service = FireAssayResultService(app_session)
        screen = service.create_solution_finish(
            solution_input(
                sample_id=a_sample.id,
                concentration=Decimal("300"),
                upper_calibration_limit=Decimal("300"),
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()
        assert screen.method.value == "fire_assay_aas"

        referee = service.create(
            result_input(
                sample_id=a_sample.id,
                gold_bead_mg=Decimal("9.000"),
                supersedes_id=screen.id,
                superseded_reason="AAS screen at the top of its calibration range; "
                "re-assayed gravimetrically",
            ),
            analyst=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()

        assert referee.supersedes_id == screen.id
        assert referee.method.value == "fire_assay_gravimetric"
        current = current_result(app_session, a_sample.id)
        assert current is not None
        assert current.id == referee.id


class TestAppendOnly:
    def test_the_application_role_cannot_update_a_result(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        from sqlalchemy import text

        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("UPDATE fire_assay_result SET au_value = 999 WHERE id = :id"),
                {"id": result.id},
            )

    def test_the_application_role_cannot_delete_a_result(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(sample_id=a_sample.id), analyst=analyst, actor_role=Role.ANALYST
        )
        app_session.flush()

        from sqlalchemy import text

        with pytest.raises(ProgrammingError, match="permission denied"):
            app_session.execute(
                text("DELETE FROM fire_assay_result WHERE id = :id"), {"id": result.id}
            )


class TestCurrentResultQuery:
    """`current_result` is the one definition of "the sample's grade" that
    result entry, certificate issuance, and the lookup screens all share.
    Expressed as an anti-join, it must still answer correctly over a chain
    of any length."""

    def test_a_three_link_chain_reports_only_its_head(
        self, app_session: Session, analyst: LabUser, supervisor: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(sample_id=a_sample.id, gold_bead_mg=Decimal("0.150")),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        second = service.create(
            result_input(
                sample_id=a_sample.id,
                gold_bead_mg=Decimal("0.160"),
                supersedes_id=first.id,
                superseded_reason="re-weighed once",
            ),
            analyst=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        third = service.create(
            result_input(
                sample_id=a_sample.id,
                gold_bead_mg=Decimal("0.170"),
                supersedes_id=second.id,
                superseded_reason="re-weighed twice",
            ),
            analyst=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()

        current = current_result(app_session, a_sample.id)
        assert current is not None
        assert current.id == third.id


class CupelledChain:
    """Shared setup for crucible wiring: a sample charged into a real batch
    through ``BatchService`` and walked to ``CUPELLED``, at a 45 g portion —
    deliberately not the recipe's 30 g nominal, so a derived weight is
    distinguishable from a re-typed one."""

    @staticmethod
    def make(app_session: Session, analyst: LabUser) -> tuple[Sample, Crucible]:
        from msa_lims.batches.service import BatchInput, BatchService, CrucibleChargeInput
        from msa_lims.db.models import Client, FluxRecipe, Submission
        from msa_lims.domain.enums import BatchStatus, MatrixType

        client = Client(code="MSA-CRUC", name="MSA Crucible Test Mining Co")
        app_session.add(client)
        app_session.flush()
        submission = Submission(
            submission_number="SUB-2026-9500",
            client_id=client.id,
            received_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
        app_session.add(submission)
        app_session.flush()
        sample = Sample(
            sample_id="MSA-24-SO-00500",
            submission_id=submission.id,
            sample_type=SampleType.SOIL,
            # Charging now requires the real READY_FOR_ASSAY status (see
            # batches/service.py's module docstring) — set up directly since
            # this helper is testing crucible wiring, not the prep walk.
            status=SampleStatus.READY_FOR_ASSAY,
        )
        app_session.add(sample)

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

        batches = BatchService(app_session, furnace_rows=6, furnace_columns=6)
        batch = batches.create_batch(
            BatchInput(opened_at=datetime(2026, 8, 25, 8, 0, tzinfo=UTC)),
            opened_by=analyst,
            actor_role=Role.ANALYST,
        )
        batches.advance_status(
            batch.id,
            target=BatchStatus.CHARGING,
            advanced_by=analyst,
            actor_role=Role.ANALYST,
        )
        crucible = batches.charge_crucible(
            CrucibleChargeInput(
                batch_id=batch.id,
                sample_id=sample.id,
                qc_material_id=None,
                flux_recipe_id=recipe.id,
                position_row=2,
                position_col=3,
                sample_weight_g=Decimal("45"),
                charged_at=datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        for target in (
            BatchStatus.IN_FUSION,
            BatchStatus.FUSED,
            BatchStatus.IN_CUPELLATION,
            BatchStatus.CUPELLED,
        ):
            batches.advance_status(
                batch.id, target=target, advanced_by=analyst, actor_role=Role.ANALYST
            )
        app_session.flush()
        return sample, crucible

    @staticmethod
    def walk_to(
        app_session: Session, analyst: LabUser, crucible: Crucible, status: CrucibleStatus
    ) -> None:
        """Force a charged crucible to an arbitrary status for refusal tests."""
        crucible.status = status
        app_session.flush()

    @staticmethod
    def part(app_session: Session, analyst: LabUser, crucible: Crucible) -> None:
        """Record parting through the real write path (CUPELLED -> PARTED)."""
        from msa_lims.batches.service import BatchService, CruciblePartingInput

        BatchService(app_session, furnace_rows=6, furnace_columns=6).record_parting(
            crucible.batch_id,
            crucible.id,
            CruciblePartingInput(
                lead_button_weight_mg=Decimal("27.8"),
                prill_weight_mg=Decimal("0.512"),
                parting_acid_volume_ml=Decimal("5"),
                parted_at=datetime(2026, 8, 25, 9, 30, tzinfo=UTC),
            ),
            parted_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

    @staticmethod
    def weigh(
        app_session: Session,
        analyst: LabUser,
        crucible: Crucible,
        *,
        bead: Decimal = Decimal("0.225"),
    ) -> None:
        """Record parting + the final weighing through the real write paths
        (CUPELLED -> PARTED -> WEIGHED), storing ``bead`` on the crucible."""
        from msa_lims.batches.service import BatchService, CrucibleWeighingInput

        CupelledChain.part(app_session, analyst, crucible)
        BatchService(app_session, furnace_rows=6, furnace_columns=6).record_weighing(
            crucible.batch_id,
            crucible.id,
            CrucibleWeighingInput(
                gold_bead_mg=bead, weighed_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
            ),
            weighed_by=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()


class TestWiringAResultToItsCrucible:
    def test_a_named_crucible_derives_the_portion_from_its_recorded_charge(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        sample, crucible = CupelledChain.make(app_session, analyst)
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(
                sample_id=sample.id,
                gold_bead_mg=Decimal("0.225"),
                sample_weight_g=None,
                crucible_id=crucible.id,
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        # The stored portion is the charge as weighed (45 g), not anything
        # typed into this request -- and 0.225 mg from 45 g is exactly 5 g/t.
        assert result.sample_weight_g == Decimal("45")
        assert result.au_value == Decimal("5")
        assert result.crucible_id == crucible.id

    def test_naming_a_crucible_and_retyping_its_portion_is_refused(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        sample, crucible = CupelledChain.make(app_session, analyst)
        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="not both"):
            service.create(
                result_input(
                    sample_id=sample.id,
                    sample_weight_g=Decimal("30"),
                    crucible_id=crucible.id,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_result_with_neither_a_crucible_nor_a_weight_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        with pytest.raises(
            FireAssayResultValidationError, match="sample_weight_g is required unless"
        ):
            service.create(
                result_input(sample_id=a_sample.id, sample_weight_g=None),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_an_unknown_crucible_is_refused_as_missing(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        from msa_lims.fire_assay_results.service import CrucibleNotFoundError

        service = FireAssayResultService(app_session)
        with pytest.raises(CrucibleNotFoundError, match="no crucible with id 999999"):
            service.create(
                result_input(
                    sample_id=a_sample.id,
                    sample_weight_g=None,
                    crucible_id=999_999,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_crucible_charged_with_another_sample_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        _charged_sample, crucible = CupelledChain.make(app_session, analyst)
        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="charged with sample"):
            service.create(
                result_input(
                    sample_id=a_sample.id,
                    sample_weight_g=None,
                    crucible_id=crucible.id,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    @pytest.mark.parametrize("early", [CrucibleStatus.CHARGED, CrucibleStatus.FUSED])
    def test_a_crucible_that_has_not_been_cupelled_produces_no_bead(
        self, app_session: Session, analyst: LabUser, early: CrucibleStatus
    ) -> None:
        sample, crucible = CupelledChain.make(app_session, analyst)
        CupelledChain.walk_to(app_session, analyst, crucible, early)
        service = FireAssayResultService(app_session)
        with pytest.raises(
            FireAssayResultValidationError, match="a bead exists only after cupellation"
        ):
            service.create(
                result_input(
                    sample_id=sample.id,
                    gold_bead_mg=Decimal("0.225"),
                    sample_weight_g=None,
                    crucible_id=crucible.id,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_rejected_crucible_produces_no_bead(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        sample, crucible = CupelledChain.make(app_session, analyst)
        CupelledChain.walk_to(app_session, analyst, crucible, CrucibleStatus.REJECTED)
        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="a bead exists only after"):
            service.create(
                result_input(
                    sample_id=sample.id,
                    gold_bead_mg=Decimal("0.225"),
                    sample_weight_g=None,
                    crucible_id=crucible.id,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_the_audit_event_names_the_crucible(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        sample, crucible = CupelledChain.make(app_session, analyst)
        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(
                sample_id=sample.id,
                gold_bead_mg=Decimal("0.225"),
                sample_weight_g=None,
                crucible_id=crucible.id,
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "fire_assay_result", AuditEvent.record_id == result.id
            )
        )
        assert event is not None
        assert event.after["crucible_id"] == crucible.id

    def test_superseding_restates_the_same_crucible_and_rederives_the_portion(
        self, app_session: Session, analyst: LabUser, supervisor: LabUser
    ) -> None:
        sample, crucible = CupelledChain.make(app_session, analyst)
        service = FireAssayResultService(app_session)
        first = service.create(
            result_input(
                sample_id=sample.id,
                gold_bead_mg=Decimal("0.225"),
                sample_weight_g=None,
                crucible_id=crucible.id,
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        second = service.create(
            result_input(
                sample_id=sample.id,
                gold_bead_mg=Decimal("0.230"),
                sample_weight_g=None,
                balance_sensitivity_mg=None,
                analysed_at=datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
                supersedes_id=first.id,
                superseded_reason="transcription error at the balance",
                crucible_id=crucible.id,
            ),
            analyst=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()

        assert second.sample_weight_g == Decimal("45")
        assert second.crucible_id == crucible.id
        current = current_result(app_session, sample.id)
        assert current is not None and current.id == second.id

    def test_entering_against_a_crucible_does_not_advance_its_status(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        """Parting and weighing are per-crucible measurements with their own
        future write path; typing a bead in must not silently invent them."""
        sample, crucible = CupelledChain.make(app_session, analyst)
        service = FireAssayResultService(app_session)
        service.create(
            result_input(
                sample_id=sample.id,
                gold_bead_mg=Decimal("0.225"),
                sample_weight_g=None,
                crucible_id=crucible.id,
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        app_session.refresh(crucible)
        assert crucible.status is CrucibleStatus.CUPELLED

    def test_a_weighed_crucible_supplies_the_portion_and_the_bead(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        """The full provenance chain: charge 45 g -> part -> weigh 0.225 mg ->
        a result naming the crucible carries *both* recorded numbers, typed
        nowhere in its own request."""
        sample, crucible = CupelledChain.make(app_session, analyst)
        CupelledChain.weigh(app_session, analyst, crucible)

        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(
                sample_id=sample.id,
                gold_bead_mg=None,
                sample_weight_g=None,
                crucible_id=crucible.id,
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert result.sample_weight_g == Decimal("45")
        assert result.gold_bead_mg == Decimal("0.225")
        assert result.crucible_id == crucible.id
        # 0.225 mg from exactly 45 g is exactly 5 g/t.
        assert result.au_value == Decimal("5")

    def test_retyping_the_bead_alongside_a_weighed_crucible_is_refused(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        sample, crucible = CupelledChain.make(app_session, analyst)
        CupelledChain.weigh(app_session, analyst, crucible)

        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="already been weighed"):
            service.create(
                result_input(
                    sample_id=sample.id,
                    gold_bead_mg=Decimal("0.300"),
                    sample_weight_g=None,
                    crucible_id=crucible.id,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_parted_but_unweighed_crucible_still_takes_a_typed_bead(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        """The boundary between the two paths: until the crucible's own
        weighing is on record there is nothing to derive, so the typed bead
        remains honest input rather than a contradiction."""
        sample, crucible = CupelledChain.make(app_session, analyst)
        CupelledChain.part(app_session, analyst, crucible)

        service = FireAssayResultService(app_session)
        result = service.create(
            result_input(
                sample_id=sample.id,
                gold_bead_mg=Decimal("0.225"),
                sample_weight_g=None,
                crucible_id=crucible.id,
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert result.sample_weight_g == Decimal("45")
        assert result.gold_bead_mg == Decimal("0.225")

    def test_direct_entry_without_a_bead_is_refused(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="gold_bead_mg is required"):
            service.create(
                result_input(sample_id=a_sample.id, gold_bead_mg=None),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )


class TestResultsNeverNameQcCrucibles:
    """A QC insertion holds no sample, so no sample result can name it at any
    stage — its bead is judged by QC Sentinel on export, not entered here."""

    @staticmethod
    def cupelled_qc_crucible(app_session: Session, analyst: LabUser) -> Crucible:
        from msa_lims.batches.service import BatchInput, BatchService, CrucibleChargeInput
        from msa_lims.db.models import FluxRecipe, QcMaterial
        from msa_lims.domain.enums import BatchStatus, MatrixType, QcMaterialType

        material = QcMaterial(
            name="OREAS 501d",
            qc_type=QcMaterialType.CRM,
            certified_au_value_g_t=Decimal("1.54"),
            certified_au_uncertainty_g_t=Decimal("0.06"),
        )
        app_session.add(material)
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

        batches = BatchService(app_session, furnace_rows=6, furnace_columns=6)
        batch = batches.create_batch(
            BatchInput(opened_at=datetime(2026, 8, 25, 8, 0, tzinfo=UTC)),
            opened_by=analyst,
            actor_role=Role.ANALYST,
        )
        batches.advance_status(
            batch.id, target=BatchStatus.CHARGING, advanced_by=analyst, actor_role=Role.ANALYST
        )
        crucible = batches.charge_crucible(
            CrucibleChargeInput(
                batch_id=batch.id,
                sample_id=None,
                qc_material_id=material.id,
                flux_recipe_id=recipe.id,
                position_row=1,
                position_col=1,
                sample_weight_g=Decimal("30"),
                charged_at=datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
            ),
            charged_by=analyst,
            actor_role=Role.ANALYST,
        )
        for target in (
            BatchStatus.IN_FUSION,
            BatchStatus.FUSED,
            BatchStatus.IN_CUPELLATION,
            BatchStatus.CUPELLED,
        ):
            batches.advance_status(
                batch.id, target=target, advanced_by=analyst, actor_role=Role.ANALYST
            )
        app_session.flush()
        return crucible

    def test_a_result_naming_a_cupelled_qc_crucible_is_refused_naming_both(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        crucible = self.cupelled_qc_crucible(app_session, analyst)
        service = FireAssayResultService(app_session)
        with pytest.raises(FireAssayResultValidationError, match="holds a QC material"):
            service.create(
                result_input(
                    sample_id=a_sample.id,
                    gold_bead_mg=Decimal("0.225"),
                    sample_weight_g=None,
                    crucible_id=crucible.id,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )
