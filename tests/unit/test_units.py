"""Units and conversion, with attention to the mass dimension this system adds."""

from __future__ import annotations

from decimal import Decimal

import pytest

from msa_lims.domain.units import (
    Dimension,
    IncompatibleUnitsError,
    Unit,
    UnknownUnitError,
    convert,
    dimension_of,
    parse_unit,
)


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("ppm", Unit.PPM),
            ("PPM", Unit.PPM),
            ("  mg/kg  ", Unit.PPM),
            ("g/t", Unit.G_PER_TONNE),
            ("gpt", Unit.G_PER_TONNE),
            ("oz/t", Unit.OZ_PER_TON),
            ("%", Unit.PERCENT),
            ("g", Unit.GRAM),
            ("mg", Unit.MILLIGRAM),
            ("kg", Unit.KILOGRAM),
            ("µg", Unit.MICROGRAM),
        ],
    )
    def test_reads_the_spellings_instruments_and_forms_use(self, text: str, expected: Unit) -> None:
        assert parse_unit(text) is expected

    def test_an_unknown_unit_is_refused_rather_than_guessed(self) -> None:
        with pytest.raises(UnknownUnitError, match="grains/ton"):
            parse_unit("grains/ton")


class TestDimensions:
    def test_grade_units_are_mass_fraction(self) -> None:
        assert dimension_of(Unit.G_PER_TONNE) is Dimension.MASS_FRACTION
        assert dimension_of(Unit.PPM) is Dimension.MASS_FRACTION

    def test_weight_units_are_mass(self) -> None:
        assert dimension_of(Unit.GRAM) is Dimension.MASS
        assert dimension_of(Unit.MILLIGRAM) is Dimension.MASS

    def test_a_bead_weight_cannot_be_converted_into_a_grade(self) -> None:
        """The conversion that needs the sample weight to be meaningful.

        Turning milligrams into grams per tonne is a calculation with a named
        input, not a unit conversion, and letting it happen here would hide the
        missing denominator.
        """
        with pytest.raises(IncompatibleUnitsError, match="without physical assumptions"):
            convert(Decimal("1"), Unit.MILLIGRAM, Unit.G_PER_TONNE)

    def test_mass_and_concentration_do_not_mix(self) -> None:
        with pytest.raises(IncompatibleUnitsError):
            convert(Decimal("1"), Unit.GRAM, Unit.MG_PER_L)


class TestConversion:
    def test_ppm_and_grams_per_tonne_are_the_same_quantity(self) -> None:
        assert convert(Decimal("5.5"), Unit.PPM, Unit.G_PER_TONNE) == Decimal("5.5")

    def test_percent_to_ppm(self) -> None:
        assert convert(Decimal("1"), Unit.PERCENT, Unit.PPM) == Decimal("10000")

    def test_ppb_to_ppm(self) -> None:
        assert convert(Decimal("1500"), Unit.PPB, Unit.PPM) == Decimal("1.5")

    def test_one_ounce_per_ton_is_240_over_7_grams_per_tonne(self) -> None:
        """Exactly, not 34.2857142857."""
        result = convert(Decimal("1"), Unit.OZ_PER_TON, Unit.G_PER_TONNE)
        assert result * 7 == Decimal("240")

    def test_grams_to_milligrams(self) -> None:
        assert convert(Decimal("0.03"), Unit.GRAM, Unit.MILLIGRAM) == Decimal("30")

    def test_kilograms_to_grams(self) -> None:
        assert convert(Decimal("2.5"), Unit.KILOGRAM, Unit.GRAM) == Decimal("2500")

    def test_converting_to_the_same_unit_is_the_identity(self) -> None:
        value = Decimal("1.2345678901234567890")
        assert convert(value, Unit.PPM, Unit.PPM) is value

    def test_precision_does_not_depend_on_the_caller_s_context(self) -> None:
        """A pinned context is what makes a verdict reproducible on another
        machine years later."""
        from decimal import localcontext

        with localcontext() as ctx:
            ctx.prec = 5
            tight = convert(Decimal("1"), Unit.OZ_PER_TON, Unit.G_PER_TONNE)
        with localcontext() as ctx:
            ctx.prec = 50
            loose = convert(Decimal("1"), Unit.OZ_PER_TON, Unit.G_PER_TONNE)
        assert tight == loose
