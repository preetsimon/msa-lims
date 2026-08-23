"""Measured values: the censored-value type that keeps `<0.01` honest."""

from __future__ import annotations

from decimal import Decimal

import pytest

from msa_lims.domain.units import Unit
from msa_lims.domain.values import (
    CensoredValueError,
    MeasuredValue,
    Substitution,
    ValueParseError,
)


class TestParsing:
    def test_a_plain_number_is_detected(self) -> None:
        value = MeasuredValue.parse("1.42", Unit.G_PER_TONNE)
        assert value.is_detected
        assert value.require_detected() == Decimal("1.42")

    @pytest.mark.parametrize("token", ["<0.01", "< 0.01", "<=0.01"])
    def test_a_less_than_token_is_a_non_detect_carrying_its_limit(self, token: str) -> None:
        value = MeasuredValue.parse(token, Unit.G_PER_TONNE)
        assert value.censored
        assert value.detection_limit == Decimal("0.01")

    def test_a_bare_nd_is_refused_because_it_carries_no_limit(self) -> None:
        """'ND' says the result was below something, without saying below what.
        Storing it would leave a value nothing downstream can bound."""
        with pytest.raises(ValueParseError, match="must carry their limit"):
            MeasuredValue.parse("ND", Unit.G_PER_TONNE)

    def test_an_empty_token_is_refused(self) -> None:
        with pytest.raises(ValueParseError, match="empty"):
            MeasuredValue.parse("   ", Unit.G_PER_TONNE)


class TestTheRefusalToFlatten:
    def test_a_non_detect_has_no_float(self) -> None:
        """The property the whole type exists for: a censored value cannot
        drift into arithmetic by accident."""
        value = MeasuredValue.non_detect(Decimal("0.01"), Unit.G_PER_TONNE)
        assert not hasattr(value, "__float__")

    def test_require_detected_raises_and_names_the_limit(self) -> None:
        value = MeasuredValue.non_detect(Decimal("0.01"), Unit.G_PER_TONNE)
        with pytest.raises(CensoredValueError) as caught:
            value.require_detected("a composite grade")
        message = str(caught.value)
        assert "a composite grade" in message
        assert "<0.01" in message

    def test_substitution_must_be_named(self) -> None:
        value = MeasuredValue.non_detect(Decimal("0.01"), Unit.G_PER_TONNE)
        assert value.substituted(Substitution.ZERO) == Decimal("0")
        assert value.substituted(Substitution.HALF_DETECTION_LIMIT) == Decimal("0.005")
        assert value.substituted(Substitution.DETECTION_LIMIT) == Decimal("0.01")

    def test_substitution_leaves_detected_values_alone(self) -> None:
        value = MeasuredValue.detected(Decimal("1.42"), Unit.G_PER_TONNE)
        assert value.substituted(Substitution.ZERO) == Decimal("1.42")


class TestConstruction:
    def test_a_censored_value_needs_a_limit(self) -> None:
        with pytest.raises(ValueError, match="requires a detection limit"):
            MeasuredValue(unit=Unit.PPM, value=None, detection_limit=None, censored=True)

    def test_a_censored_value_may_not_also_carry_a_measurement(self) -> None:
        with pytest.raises(ValueError, match="cannot carry"):
            MeasuredValue(
                unit=Unit.PPM,
                value=Decimal("1"),
                detection_limit=Decimal("0.01"),
                censored=True,
            )

    def test_a_detected_value_needs_a_measurement(self) -> None:
        with pytest.raises(ValueError, match="requires a measurement"):
            MeasuredValue(unit=Unit.PPM, value=None, detection_limit=None, censored=False)


class TestConversion:
    def test_the_detection_limit_converts_with_the_value(self) -> None:
        """A limit left behind in the old unit reads as a real analytical
        finding, which is why it is the same operation."""
        value = MeasuredValue.detected(Decimal("1500"), Unit.PPB, Decimal("50"))
        converted = value.converted_to(Unit.PPM)
        assert converted.require_detected() == Decimal("1.5")
        assert converted.detection_limit == Decimal("0.05")

    def test_a_non_detect_converts_its_limit(self) -> None:
        value = MeasuredValue.non_detect(Decimal("50"), Unit.PPB)
        converted = value.converted_to(Unit.PPM)
        assert converted.censored
        assert converted.detection_limit == Decimal("0.05")


class TestRendering:
    def test_a_non_detect_renders_as_it_was_reported(self) -> None:
        value = MeasuredValue.non_detect(Decimal("0.01"), Unit.G_PER_TONNE)
        assert str(value) == "<0.01 g/t"

    def test_a_detected_value_keeps_its_trailing_zeros(self) -> None:
        """Significant figures are an analytical statement: 1.40 g/t says
        something 1.4 g/t does not."""
        value = MeasuredValue.detected(Decimal("1.40"), Unit.G_PER_TONNE)
        assert str(value) == "1.40 g/t"
