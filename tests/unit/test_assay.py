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
