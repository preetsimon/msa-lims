"""How a computed grade is rendered on a signed certificate.

This is the fix for a real defect caught during live verification: a grade
whose division does not terminate (0.160 mg over 30 g, for instance) was
printing thirty-four digits of `Decimal` division artifact straight onto a
client-facing PDF — ``5.333333333333333333333333333333333 g/t`` — rather than
a number any lab would actually report.
"""

from __future__ import annotations

from decimal import Decimal

from msa_lims.certificates.service import _display_grade
from msa_lims.domain.units import Unit
from msa_lims.domain.values import MeasuredValue


class TestDisplayGrade:
    def test_a_non_terminating_division_is_rounded_to_three_decimal_places(self) -> None:
        # 0.160 mg over 30 g, exactly what live verification produced.
        measured = MeasuredValue.detected(
            Decimal("5.333333333333333333333333333333333"), Unit.G_PER_TONNE
        )
        assert _display_grade(measured) == "5.333 g/t"

    def test_a_value_that_already_terminates_cleanly_is_unaffected(self) -> None:
        measured = MeasuredValue.detected(Decimal("5.000"), Unit.G_PER_TONNE)
        assert _display_grade(measured) == "5.000 g/t"

    def test_rounding_is_half_even_matching_the_rest_of_the_codebase(self) -> None:
        # domain/units.py's convert() documents ROUND_HALF_EVEN as the fixed
        # convention so the same input always rounds the same way regardless
        # of caller context; certificate display follows the same rule.
        measured = MeasuredValue.detected(Decimal("5.3335"), Unit.G_PER_TONNE)
        assert _display_grade(measured) == "5.334 g/t"  # 3 rounds up to even 4
        measured_down = MeasuredValue.detected(Decimal("5.3325"), Unit.G_PER_TONNE)
        assert _display_grade(measured_down) == "5.332 g/t"  # 2 rounds down to even 2

    def test_a_non_detect_is_unaffected_by_rounding(self) -> None:
        """Detection limits are set values, not division artifacts -- a
        censored result should render exactly as entered."""
        measured = MeasuredValue.non_detect(Decimal("0.0333333333"), Unit.G_PER_TONNE)
        assert _display_grade(measured) == "<0.0333333333 g/t"

    def test_the_underlying_stored_precision_is_never_touched(self) -> None:
        """Rounding happens only at the point a human reads the number."""
        full_precision = Decimal("5.333333333333333333333333333333333")
        measured = MeasuredValue.detected(full_precision, Unit.G_PER_TONNE)
        _display_grade(measured)
        assert measured.value == full_precision
