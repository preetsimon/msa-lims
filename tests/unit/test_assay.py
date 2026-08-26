"""The grade calculation, including the ways it is asked to be wrong."""

from __future__ import annotations

from decimal import Decimal

import pytest

from msa_lims.domain.assay import (
    AssayCalculationError,
    assay_ton_grams,
    bead_weight_for_grade,
    grade_in_ounces_per_ton,
    gravimetric_grade,
    silver_by_difference,
    solution_finish_grade,
)
from msa_lims.domain.units import Unit


class TestAssayTon:
    def test_is_the_exact_rational_not_a_rounded_constant(self) -> None:
        assert assay_ton_grams() * 6 == Decimal(175)

    def test_one_milligram_from_one_assay_ton_is_one_ounce_per_ton(self) -> None:
        """The definition the assay ton exists for.

        If this ever fails, either the constant was rounded or the oz/t
        conversion factor drifted — and a whole century of North American
        grade reporting is built on this identity holding exactly.
        """
        grade = gravimetric_grade(gold_bead_mg=Decimal("1"), sample_weight_g=assay_ton_grams())
        in_ounces = grade_in_ounces_per_ton(grade)
        assert in_ounces.require_detected().quantize(Decimal("0.000000001")) == Decimal(
            "1.000000000"
        )


class TestGravimetricGrade:
    def test_a_bead_from_a_thirty_gram_portion(self) -> None:
        # 0.150 mg of gold from 30 g of rock is 5.00 g/t.
        grade = gravimetric_grade(gold_bead_mg=Decimal("0.150"), sample_weight_g=Decimal("30"))
        assert grade.unit is Unit.G_PER_TONNE
        assert grade.require_detected() == Decimal("5")

    def test_a_bead_below_balance_sensitivity_is_a_non_detect(self) -> None:
        """Not a very small grade — no grade.

        This is the whole reason MeasuredValue exists. A 0.0005 mg reading on a
        balance that resolves 0.001 mg is noise, and reporting it as 0.0167 g/t
        would put a number on a certificate that the instrument never measured.
        """
        grade = gravimetric_grade(
            gold_bead_mg=Decimal("0.0005"),
            sample_weight_g=Decimal("30"),
            balance_sensitivity_mg=Decimal("0.001"),
        )
        assert grade.censored
        assert not grade.is_detected
        # 0.001 mg in 30 g is 0.0333… g/t.
        assert grade.detection_limit is not None
        assert grade.detection_limit.quantize(Decimal("0.0001")) == Decimal("0.0333")
        assert str(grade).startswith("<0.0333")

    def test_a_bead_exactly_at_sensitivity_is_still_a_non_detect(self) -> None:
        """The boundary is inclusive: at the limit you cannot distinguish it
        from the limit."""
        grade = gravimetric_grade(
            gold_bead_mg=Decimal("0.001"),
            sample_weight_g=Decimal("30"),
            balance_sensitivity_mg=Decimal("0.001"),
        )
        assert grade.censored

    def test_a_detected_grade_still_carries_its_detection_limit(self) -> None:
        grade = gravimetric_grade(
            gold_bead_mg=Decimal("0.150"),
            sample_weight_g=Decimal("30"),
            balance_sensitivity_mg=Decimal("0.001"),
        )
        assert grade.is_detected
        assert grade.detection_limit is not None

    def test_a_zero_sample_weight_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="sample weight"):
            gravimetric_grade(gold_bead_mg=Decimal("0.1"), sample_weight_g=Decimal("0"))

    def test_a_negative_bead_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="negative"):
            gravimetric_grade(gold_bead_mg=Decimal("-0.1"), sample_weight_g=Decimal("30"))

    def test_a_zero_bead_without_sensitivity_is_refused(self) -> None:
        """A 0 mg reading with no stated sensitivity cannot be told apart
        from one below what the balance resolves — reporting it as a detected
        0 g/t would flatten the distinction MeasuredValue exists to keep."""
        with pytest.raises(AssayCalculationError, match="balance_sensitivity_mg"):
            gravimetric_grade(gold_bead_mg=Decimal("0"), sample_weight_g=Decimal("30"))

    def test_a_zero_bead_with_sensitivity_is_a_non_detect(self) -> None:
        """The remedy the refusal names: state the sensitivity and the same
        reading becomes a non-detect at the grade it corresponds to."""
        grade = gravimetric_grade(
            gold_bead_mg=Decimal("0"),
            sample_weight_g=Decimal("30"),
            balance_sensitivity_mg=Decimal("0.001"),
        )
        assert grade.censored
        assert grade.detection_limit is not None


class TestSilverByDifference:
    def test_silver_is_what_parting_removed(self) -> None:
        silver = silver_by_difference(
            dore_bead_mg=Decimal("2.000"),
            gold_bead_mg=Decimal("0.500"),
            sample_weight_g=Decimal("30"),
        )
        # 1.5 mg of silver in 30 g is 50 g/t.
        assert silver.require_detected() == Decimal("50")

    def test_transposed_weights_are_refused_rather_than_reported_negative(self) -> None:
        """The failure mode this guard exists for.

        A gold bead heavier than its doré parent is impossible. Without the
        check it yields a negative silver grade, which rounds to zero in a
        report and is never questioned.
        """
        with pytest.raises(AssayCalculationError, match="transposed"):
            silver_by_difference(
                dore_bead_mg=Decimal("0.500"),
                gold_bead_mg=Decimal("2.000"),
                sample_weight_g=Decimal("30"),
            )

    def test_a_fully_parted_bead_leaves_no_silver(self) -> None:
        silver = silver_by_difference(
            dore_bead_mg=Decimal("1.000"),
            gold_bead_mg=Decimal("1.000"),
            sample_weight_g=Decimal("30"),
        )
        assert silver.require_detected() == Decimal("0")


class TestSolutionFinishGrade:
    def test_a_reading_from_a_thirty_gram_portion(self) -> None:
        # 1.5 mg/L in a 10 mL flask is 15 ug of gold; from a 30 g portion
        # that is 0.5 ug/g, which is 0.5 g/t.
        grade = solution_finish_grade(
            concentration=Decimal("1.5"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
        )
        assert grade.unit is Unit.G_PER_TONNE
        assert grade.require_detected() == Decimal("0.5")

    def test_agrees_with_gravimetric_on_the_same_bead(self) -> None:
        """The two finishes measure the same physical bead two ways; on a
        bead small enough to stay on-curve, they must agree on its grade.

        A 0.150 mg bead dissolved into 10 mL is 15 mg/L (0.150 mg in 0.01 L) —
        read that back and the result should be the gravimetric path's own
        5 g/t."""
        gravimetric = gravimetric_grade(
            gold_bead_mg=Decimal("0.150"), sample_weight_g=Decimal("30")
        )
        solution = solution_finish_grade(
            concentration=Decimal("15"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
        )
        assert solution.require_detected() == gravimetric.require_detected() == Decimal("5")

    def test_ug_per_l_converts_to_the_identical_grade_as_mg_per_l(self) -> None:
        via_mg = solution_finish_grade(
            concentration=Decimal("1.5"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
        )
        via_ug = solution_finish_grade(
            concentration=Decimal("1500"),
            concentration_unit=Unit.UG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
        )
        assert via_ug.require_detected() == via_mg.require_detected()

    def test_a_reading_at_or_below_detection_limit_is_a_non_detect(self) -> None:
        grade = solution_finish_grade(
            concentration=Decimal("0.01"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
            detection_limit=Decimal("0.03"),
        )
        assert grade.censored
        assert grade.detection_limit is not None
        # 0.03 mg/L in a 10 mL flask, from a 30 g portion, is 0.01 g/t —
        # ALS's own published lower limit for a 30 g AAS finish.
        assert grade.detection_limit.quantize(Decimal("0.0001")) == Decimal("0.0100")

    def test_a_reading_above_the_calibration_range_is_refused(self) -> None:
        """The failure mode this guard exists for: past the top standard the
        instrument is extrapolating, and the printed number is not a grade —
        it is a data point off the end of a line that was never drawn there."""
        with pytest.raises(AssayCalculationError, match="calibration range"):
            solution_finish_grade(
                concentration=Decimal("400"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
                upper_calibration_limit=Decimal("300"),
            )

    def test_a_reading_exactly_at_the_calibration_limit_is_accepted(self) -> None:
        """The boundary is inclusive on the accepting side — unlike a balance's
        sensitivity floor, the top standard itself was actually run."""
        grade = solution_finish_grade(
            concentration=Decimal("300"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
            upper_calibration_limit=Decimal("300"),
        )
        assert grade.is_detected

    def test_a_mass_fraction_unit_is_refused(self) -> None:
        """ppm describes a solid, not what is in the flask — see the
        function's own docstring for why the two must not be conflated."""
        with pytest.raises(AssayCalculationError, match="mass concentration"):
            solution_finish_grade(
                concentration=Decimal("1.5"),
                concentration_unit=Unit.PPM,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
            )

    def test_a_zero_reading_without_detection_limit_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="detection_limit"):
            solution_finish_grade(
                concentration=Decimal("0"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
            )

    def test_a_negative_concentration_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="negative"):
            solution_finish_grade(
                concentration=Decimal("-0.1"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
            )

    def test_a_zero_sample_weight_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="sample weight"):
            solution_finish_grade(
                concentration=Decimal("1.5"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("0"),
            )

    def test_a_zero_solution_volume_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="solution volume"):
            solution_finish_grade(
                concentration=Decimal("1.5"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("0"),
                sample_weight_g=Decimal("30"),
            )


class TestInverseCalculation:
    def test_round_trips_with_the_forward_calculation(self) -> None:
        bead = bead_weight_for_grade(
            grade_g_per_tonne=Decimal("12.4"), sample_weight_g=Decimal("30")
        )
        grade = gravimetric_grade(gold_bead_mg=bead, sample_weight_g=Decimal("30"))
        assert grade.require_detected() == Decimal("12.4")

    def test_predicts_an_unweighably_small_bead_before_the_furnace_runs(self) -> None:
        bead = bead_weight_for_grade(
            grade_g_per_tonne=Decimal("0.005"), sample_weight_g=Decimal("30")
        )
        assert bead < Decimal("0.001")
