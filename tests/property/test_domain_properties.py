"""Invariants that must hold for every input, not just the ones we thought of.

Example-based tests check the cases the author imagined. These check the
statements the domain actually rests on — and they are the tests that catch a
refactor of the conversion arithmetic or the interval convention.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from msa_lims.domain.assay import bead_weight_for_grade, gravimetric_grade
from msa_lims.domain.sample_id import DepthInterval, find_overlaps, parse_sample_id
from msa_lims.domain.units import Dimension, Unit, convert, dimension_of
from msa_lims.domain.values import MeasuredValue, Substitution

# Values across the range a laboratory actually reports: trace ppb through
# percent-level base metals. Bounded because unbounded Decimals explore
# exponents no balance can produce and tell us nothing.
grades = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("100000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
weights = st.decimals(
    min_value=Decimal("0.001"),
    max_value=Decimal("5000"),
    allow_nan=False,
    allow_infinity=False,
    places=3,
)
depths = st.decimals(min_value=Decimal("0"), max_value=Decimal("3000"), allow_nan=False, places=2)
#: Sample lengths as drilled: never zero, rarely over a few metres.
interval_lengths = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("50"), allow_nan=False, places=2
)

mass_fraction_units = st.sampled_from(
    [unit for unit in Unit if dimension_of(unit) is Dimension.MASS_FRACTION]
)
mass_units = st.sampled_from([unit for unit in Unit if dimension_of(unit) is Dimension.MASS])


class TestUnitConversion:
    @given(value=grades, unit=mass_fraction_units)
    def test_converting_to_the_same_unit_changes_nothing(self, value: Decimal, unit: Unit) -> None:
        assert convert(value, unit, unit) == value

    @given(value=grades, source=mass_fraction_units, target=mass_fraction_units)
    def test_conversion_within_a_dimension_never_raises(
        self, value: Decimal, source: Unit, target: Unit
    ) -> None:
        convert(value, source, target)

    @given(value=grades, source=mass_fraction_units, target=mass_fraction_units)
    def test_conversion_preserves_sign_and_zero(
        self, value: Decimal, source: Unit, target: Unit
    ) -> None:
        assert convert(value, source, target) > 0

    @given(value=grades, source=mass_fraction_units, target=mass_fraction_units)
    def test_round_trip_is_stable_to_working_precision(
        self, value: Decimal, source: Unit, target: Unit
    ) -> None:
        """Not exact — oz/t is 240/7 and does not terminate — but the error
        stays far below any reportable figure. This is the property that
        justifies storing values in the unit they were reported in."""
        back = convert(convert(value, source, target), target, source)
        assert abs(back - value) <= abs(value) * Decimal("1e-25")

    @given(value=grades, source=mass_units, target=mass_units)
    def test_mass_conversion_round_trips_exactly(
        self, value: Decimal, source: Unit, target: Unit
    ) -> None:
        """Every mass factor is a power of ten, so unlike oz/t these are exact."""
        assert convert(convert(value, source, target), target, source) == value


class TestCensoredValues:
    @given(limit=grades)
    def test_a_non_detect_is_never_detected(self, limit: Decimal) -> None:
        assert not MeasuredValue.non_detect(limit, Unit.G_PER_TONNE).is_detected

    @given(limit=grades)
    def test_every_substitution_lands_within_the_limit(self, limit: Decimal) -> None:
        """Whatever convention a lab picks, a non-detect cannot substitute to
        something above the limit it was reported against."""
        value = MeasuredValue.non_detect(limit, Unit.G_PER_TONNE)
        for strategy in Substitution:
            substituted = value.substituted(strategy)
            assert Decimal(0) <= substituted <= limit

    @given(value=grades, limit=grades)
    def test_conversion_preserves_censoring(self, value: Decimal, limit: Decimal) -> None:
        detected = MeasuredValue.detected(value, Unit.PPM, limit)
        censored = MeasuredValue.non_detect(limit, Unit.PPM)
        assert detected.converted_to(Unit.PPB).is_detected
        assert censored.converted_to(Unit.PPB).censored

    @given(token=grades.map(str))
    def test_parsing_round_trips_through_rendering(self, token: str) -> None:
        value = MeasuredValue.parse(token, Unit.G_PER_TONNE)
        assert str(value) == f"{token} g/t"


class TestGradeArithmetic:
    @given(grade=grades, weight=weights)
    @settings(max_examples=200)
    def test_the_inverse_calculation_recovers_the_grade(
        self, grade: Decimal, weight: Decimal
    ) -> None:
        bead = bead_weight_for_grade(grade_g_per_tonne=grade, sample_weight_g=weight)
        recovered = gravimetric_grade(gold_bead_mg=bead, sample_weight_g=weight)
        assert recovered.require_detected() == grade

    @given(bead=grades, weight=weights)
    def test_grade_rises_with_bead_weight_at_a_fixed_portion(
        self, bead: Decimal, weight: Decimal
    ) -> None:
        smaller = gravimetric_grade(gold_bead_mg=bead, sample_weight_g=weight)
        larger = gravimetric_grade(gold_bead_mg=bead * 2, sample_weight_g=weight)
        assert larger.require_detected() > smaller.require_detected()

    @given(bead=grades, weight=weights)
    def test_grade_falls_as_the_portion_grows_for_a_fixed_bead(
        self, bead: Decimal, weight: Decimal
    ) -> None:
        assert (
            gravimetric_grade(gold_bead_mg=bead, sample_weight_g=weight * 2).require_detected()
            < gravimetric_grade(gold_bead_mg=bead, sample_weight_g=weight).require_detected()
        )


class TestDepthIntervals:
    @given(start=depths, length=interval_lengths)
    def test_length_is_the_difference(self, start: Decimal, length: Decimal) -> None:
        interval = DepthInterval(start, start + length)
        assert interval.length_m == length

    @given(start=depths, length=interval_lengths)
    def test_an_interval_never_overlaps_the_one_that_starts_where_it_ends(
        self, start: Decimal, length: Decimal
    ) -> None:
        """The half-open convention, stated as a law: contiguous sampling is
        the normal case and must never be flagged as a conflict."""
        first = DepthInterval(start, start + length)
        second = DepthInterval(start + length, start + length * 2)
        assert not first.overlaps(second)
        assert not second.overlaps(first)

    @given(start=depths, length=interval_lengths)
    def test_an_interval_always_overlaps_itself(self, start: Decimal, length: Decimal) -> None:
        interval = DepthInterval(start, start + length)
        assert interval.overlaps(interval)

    @given(
        starts=st.lists(
            st.decimals(min_value=Decimal("0"), max_value=Decimal("500"), places=1),
            min_size=1,
            max_size=12,
            unique=True,
        )
    )
    def test_a_contiguous_run_never_reports_a_conflict(self, starts: list[Decimal]) -> None:
        ordered = sorted(starts)
        intervals = [
            (f"S{index}", DepthInterval(lower, upper))
            for index, (lower, upper) in enumerate(pairwise(ordered))
        ]
        assume(intervals)
        assert find_overlaps(intervals) == []


class TestSampleLabels:
    @given(
        code=st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=2, max_size=5),
        year=st.integers(min_value=0, max_value=99),
        hole=st.integers(min_value=0, max_value=9999),
        start=st.decimals(min_value=Decimal("0"), max_value=Decimal("900"), places=2),
        length=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("50"), places=2),
    )
    def test_a_generated_drill_label_parses_back_to_its_parts(
        self, code: str, year: int, hole: int, start: Decimal, length: Decimal
    ) -> None:
        label = f"{code}-{year:02d}-{hole:03d}-{start}_{start + length}"
        identity = parse_sample_id(label)
        assert identity.property_code == code
        assert identity.program_year == year
        assert identity.hole_number == hole
        assert identity.interval is not None
        assert identity.interval.from_depth_m == start
