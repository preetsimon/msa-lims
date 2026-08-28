"""Multi-element domain: the element_grade calculation and the Element enum."""

from __future__ import annotations

from decimal import Decimal

import pytest

from msa_lims.domain.assay import AssayCalculationError, element_grade
from msa_lims.domain.enums import Element
from msa_lims.domain.units import Unit


class TestElementEnum:
    def test_all_values_are_iupac_symbols(self) -> None:
        for member in Element:
            assert len(member.value) <= 3
            # IUPAC symbols are title case: first letter upper, rest lower.
            assert member.value[0].isupper()
            assert member.value == member.value[0] + member.value[1:].lower()

    def test_gold_is_present(self) -> None:
        assert Element.AU.value == "Au"

    def test_common_icp_elements_are_present(self) -> None:
        expected = {"Cu", "Zn", "Pb", "Fe", "Ni", "As", "Ag", "Co", "Cr", "Mn"}
        present = {e.value for e in Element}
        assert expected <= present


class TestElementGrade:
    def test_a_reading_from_a_thirty_gram_portion(self) -> None:
        # 1.5 mg/L in a 10 mL flask is 15 ug of copper; from a 30 g portion
        # that is 0.5 ug/g = 0.5 ppm.
        grade = element_grade(
            concentration=Decimal("1.5"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
        )
        assert grade.unit is Unit.PPM
        assert grade.require_detected() == Decimal("0.5")

    def test_ppb_output_unit(self) -> None:
        # Same reading but output in ppb: 0.5 ppm = 500 ppb.
        grade = element_grade(
            concentration=Decimal("1.5"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
            output_unit=Unit.PPB,
        )
        assert grade.unit is Unit.PPB
        assert grade.require_detected() == Decimal("500")

    def test_g_t_output_unit(self) -> None:
        # g/t == ppm, so the number should be the same.
        grade = element_grade(
            concentration=Decimal("1.5"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
            output_unit=Unit.G_PER_TONNE,
        )
        assert grade.unit is Unit.G_PER_TONNE
        assert grade.require_detected() == Decimal("0.5")

    def test_percent_output_unit(self) -> None:
        # 0.5 ppm = 0.00005 %
        grade = element_grade(
            concentration=Decimal("1.5"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("10"),
            sample_weight_g=Decimal("30"),
            output_unit=Unit.PERCENT,
        )
        assert grade.unit is Unit.PERCENT
        assert grade.require_detected() == Decimal("0.00005")

    def test_a_reading_at_or_below_detection_limit_is_a_non_detect(self) -> None:
        grade = element_grade(
            concentration=Decimal("0.05"),
            concentration_unit=Unit.MG_PER_L,
            solution_volume_ml=Decimal("50"),
            sample_weight_g=Decimal("30"),
            detection_limit=Decimal("0.05"),
        )
        assert grade.censored is True
        # 0.05 mg/L * 50 mL / 30 g = 0.0833... ppm
        assert grade.detection_limit is not None
        assert grade.detection_limit > 0

    def test_ug_l_concentration(self) -> None:
        # 100 ug/L = 0.1 mg/L; in 25 mL from 50 g: 0.05 ppm
        grade = element_grade(
            concentration=Decimal("100"),
            concentration_unit=Unit.UG_PER_L,
            solution_volume_ml=Decimal("25"),
            sample_weight_g=Decimal("50"),
        )
        assert grade.unit is Unit.PPM
        assert grade.require_detected() == Decimal("0.05")

    def test_zero_concentration_without_detection_limit_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="detection_limit"):
            element_grade(
                concentration=Decimal("0"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
            )

    def test_negative_concentration_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="negative"):
            element_grade(
                concentration=Decimal("-1"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
            )

    def test_ppm_as_concentration_unit_is_refused(self) -> None:
        """ppm describes a solid, not a solution — same rule as solution_finish_grade."""
        with pytest.raises(AssayCalculationError, match="mass concentration"):
            element_grade(
                concentration=Decimal("5"),
                concentration_unit=Unit.PPM,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
            )

    def test_mass_output_unit_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="mass fraction"):
            element_grade(
                concentration=Decimal("5"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("30"),
                output_unit=Unit.GRAM,
            )

    def test_negative_sample_weight_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="greater than zero"):
            element_grade(
                concentration=Decimal("5"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("10"),
                sample_weight_g=Decimal("-1"),
            )

    def test_zero_solution_volume_is_refused(self) -> None:
        with pytest.raises(AssayCalculationError, match="greater than zero"):
            element_grade(
                concentration=Decimal("5"),
                concentration_unit=Unit.MG_PER_L,
                solution_volume_ml=Decimal("0"),
                sample_weight_g=Decimal("30"),
            )
