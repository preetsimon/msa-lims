"""Flux charge scaling."""

from __future__ import annotations

from decimal import Decimal

import pytest

from msa_lims.domain.flux import FluxAmounts, FluxCalculationError, scale_flux_charge

NOMINAL = FluxAmounts(
    litharge_g=Decimal("60"),
    soda_ash_g=Decimal("90"),
    borax_g=Decimal("30"),
    silica_g=Decimal("15"),
    flour_g=Decimal("3"),
    nitre_g=Decimal("0"),
)


class TestScaling:
    def test_the_exact_nominal_weight_returns_the_recipe_unchanged(self) -> None:
        scaled = scale_flux_charge(
            NOMINAL, nominal_portion_g=Decimal("30"), sample_weight_g=Decimal("30")
        )
        assert scaled == NOMINAL

    def test_doubling_the_sample_weight_doubles_every_reagent(self) -> None:
        scaled = scale_flux_charge(
            NOMINAL, nominal_portion_g=Decimal("30"), sample_weight_g=Decimal("60")
        )
        assert scaled.litharge_g == Decimal("120")
        assert scaled.soda_ash_g == Decimal("180")
        assert scaled.borax_g == Decimal("60")
        assert scaled.silica_g == Decimal("30")
        assert scaled.flour_g == Decimal("6")

    def test_a_reagent_the_recipe_does_not_use_stays_zero(self) -> None:
        scaled = scale_flux_charge(
            NOMINAL, nominal_portion_g=Decimal("30"), sample_weight_g=Decimal("15")
        )
        assert scaled.nitre_g == Decimal("0")

    def test_a_smaller_portion_scales_down(self) -> None:
        scaled = scale_flux_charge(
            NOMINAL, nominal_portion_g=Decimal("30"), sample_weight_g=Decimal("15")
        )
        assert scaled.litharge_g == Decimal("30")


class TestInvalidInputs:
    def test_a_zero_nominal_portion_is_refused(self) -> None:
        with pytest.raises(FluxCalculationError, match="nominal portion"):
            scale_flux_charge(
                NOMINAL, nominal_portion_g=Decimal("0"), sample_weight_g=Decimal("30")
            )

    def test_a_negative_sample_weight_is_refused(self) -> None:
        with pytest.raises(FluxCalculationError, match="sample weight"):
            scale_flux_charge(
                NOMINAL, nominal_portion_g=Decimal("30"), sample_weight_g=Decimal("-1")
            )

    def test_a_zero_sample_weight_is_refused(self) -> None:
        with pytest.raises(FluxCalculationError, match="sample weight"):
            scale_flux_charge(
                NOMINAL, nominal_portion_g=Decimal("30"), sample_weight_g=Decimal("0")
            )
