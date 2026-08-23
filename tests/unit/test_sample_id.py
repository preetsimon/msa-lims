"""Sample labels and depth intervals."""

from __future__ import annotations

from decimal import Decimal

import pytest

from msa_lims.domain.sample_id import (
    DepthInterval,
    DepthIntervalError,
    SampleIdError,
    find_overlaps,
    parse_hole_id,
    parse_sample_id,
)


class TestDrillSampleLabels:
    def test_reads_property_year_hole_and_interval(self) -> None:
        identity = parse_sample_id("MSA-24-001-142.50_144.00")
        assert identity.property_code == "MSA"
        assert identity.program_year == 24
        assert identity.hole_number == 1
        assert identity.is_drill_sample
        assert identity.interval == DepthInterval(Decimal("142.50"), Decimal("144.00"))

    def test_hole_id_groups_samples_down_one_hole(self) -> None:
        assert parse_sample_id("MSA-24-001-142.50_144.00").hole_id == "MSA-24-001"
        assert parse_sample_id("MSA-24-001-144.00_145.50").hole_id == "MSA-24-001"

    def test_lowercase_input_is_normalised(self) -> None:
        assert parse_sample_id("msa-24-001-142.5_144.0").property_code == "MSA"

    def test_four_digit_hole_numbers_are_accepted(self) -> None:
        assert parse_sample_id("MSA-24-1042-10_11").hole_number == 1042


class TestSurfaceSampleLabels:
    def test_reads_medium_and_serial(self) -> None:
        identity = parse_sample_id("MSA-24-SO-00417")
        assert identity.medium_code == "SO"
        assert identity.serial == 417
        assert not identity.is_drill_sample
        assert identity.interval is None

    def test_asking_a_surface_sample_for_its_hole_is_an_error(self) -> None:
        """Rather than returning an empty string, which would silently pile
        every surface sample into one group."""
        identity = parse_sample_id("MSA-24-SO-00417")
        with pytest.raises(SampleIdError, match="not a drill sample"):
            _ = identity.hole_id


class TestUnreadableLabels:
    @pytest.mark.parametrize(
        "label",
        [
            "",
            "   ",
            "MSA-24-001",  # a hole, not a sample
            "MSA-2024-001-1_2",  # four-digit year
            "MSA-24-001-144.00_142.50",  # interval runs backwards
            "just some text",
        ],
    )
    def test_are_refused_rather_than_stored_opaquely(self, label: str) -> None:
        with pytest.raises((SampleIdError, DepthIntervalError)):
            parse_sample_id(label)

    def test_the_message_shows_both_accepted_shapes(self) -> None:
        with pytest.raises(SampleIdError) as caught:
            parse_sample_id("MSA/24/001")
        message = str(caught.value)
        assert "MSA-24-001-142.50_144.00" in message
        assert "MSA-24-SO-00417" in message


class TestDepthInterval:
    def test_length_is_the_weight_in_a_composite(self) -> None:
        assert DepthInterval(Decimal("142.50"), Decimal("144.00")).length_m == Decimal("1.50")

    def test_an_interval_must_advance_down_the_hole(self) -> None:
        with pytest.raises(DepthIntervalError, match="advance"):
            DepthInterval(Decimal("144"), Decimal("142"))

    def test_a_zero_length_interval_is_refused(self) -> None:
        with pytest.raises(DepthIntervalError):
            DepthInterval(Decimal("142"), Decimal("142"))

    def test_negative_depth_is_refused(self) -> None:
        with pytest.raises(DepthIntervalError, match="negative"):
            DepthInterval(Decimal("-1"), Decimal("2"))

    def test_contiguous_intervals_do_not_overlap(self) -> None:
        """The half-open convention earning its keep: without it, every
        contiguous sampling run in the database would report a conflict."""
        first = DepthInterval(Decimal("142.50"), Decimal("144.00"))
        second = DepthInterval(Decimal("144.00"), Decimal("145.50"))
        assert not first.overlaps(second)
        assert not second.overlaps(first)

    def test_genuine_overlap_is_detected_both_ways(self) -> None:
        first = DepthInterval(Decimal("142.50"), Decimal("144.00"))
        second = DepthInterval(Decimal("143.00"), Decimal("145.00"))
        assert first.overlaps(second)
        assert second.overlaps(first)

    def test_a_contained_interval_overlaps(self) -> None:
        outer = DepthInterval(Decimal("140"), Decimal("150"))
        inner = DepthInterval(Decimal("142"), Decimal("143"))
        assert outer.overlaps(inner)
        assert inner.overlaps(outer)


class TestFindOverlaps:
    def test_a_clean_contiguous_run_has_no_conflicts(self) -> None:
        intervals = [
            (f"MSA-24-001-{start}_{start + 2}", DepthInterval(Decimal(start), Decimal(start + 2)))
            for start in range(100, 120, 2)
        ]
        assert find_overlaps(intervals) == []

    def test_reports_every_conflicting_pair_not_just_the_first(self) -> None:
        """A geologist fixing a submission wants the whole list in one pass."""
        intervals = [
            ("A", DepthInterval(Decimal("10"), Decimal("20"))),
            ("B", DepthInterval(Decimal("15"), Decimal("25"))),
            ("C", DepthInterval(Decimal("18"), Decimal("30"))),
            ("D", DepthInterval(Decimal("50"), Decimal("60"))),
        ]
        conflicts = find_overlaps(intervals)
        assert set(conflicts) == {("A", "B"), ("A", "C"), ("B", "C")}

    def test_an_empty_submission_has_no_conflicts(self) -> None:
        assert find_overlaps([]) == []


class TestHoleIds:
    def test_reads_a_hole_label(self) -> None:
        assert parse_hole_id("MSA-24-001") == ("MSA", 24, 1)

    def test_refuses_a_sample_label(self) -> None:
        with pytest.raises(SampleIdError):
            parse_hole_id("MSA-24-001-142.50_144.00")
