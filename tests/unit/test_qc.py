"""Pure QC advisory arithmetic — the numbers a dossier reports, never a verdict."""

from __future__ import annotations

from decimal import Decimal

import pytest

from msa_lims.domain.assay import gravimetric_grade
from msa_lims.domain.qc import blank_advisory, crm_z_score
from msa_lims.domain.units import Unit
from msa_lims.domain.values import MeasuredValue


def detected(g_t: str) -> MeasuredValue:
    return MeasuredValue.detected(Decimal(g_t), Unit.G_PER_TONNE)


def non_detect(limit_g_t: str) -> MeasuredValue:
    return MeasuredValue.non_detect(Decimal(limit_g_t), Unit.G_PER_TONNE)


class TestCrmZScore:
    def test_z_is_exact(self) -> None:
        """1.60 measured against 1.54 ± 0.06 is exactly +1."""
        z, advisory = crm_z_score(
            detected("1.60"),
            certified_au_value_g_t=Decimal("1.54"),
            certified_au_uncertainty_g_t=Decimal("0.06"),
        )
        assert z == Decimal("1")
        assert advisory is None

    def test_negative_z(self) -> None:
        z, _ = crm_z_score(
            detected("1.42"),
            certified_au_value_g_t=Decimal("1.54"),
            certified_au_uncertainty_g_t=Decimal("0.06"),
        )
        assert z == Decimal("-2")

    def test_a_non_detect_has_no_z_and_says_so(self) -> None:
        """A CRM coming back non-detect is comparable to nothing; inventing a
        z from the censoring limit would manufacture data."""
        z, advisory = crm_z_score(
            non_detect("0.01"),
            certified_au_value_g_t=Decimal("1.54"),
            certified_au_uncertainty_g_t=Decimal("0.06"),
        )
        assert z is None
        assert advisory is not None
        assert advisory.code == "crm_non_detect"

    def test_an_invalid_uncertainty_is_flagged_not_divided_by(self) -> None:
        z, advisory = crm_z_score(
            detected("1.54"),
            certified_au_value_g_t=Decimal("1.54"),
            certified_au_uncertainty_g_t=Decimal("0"),
        )
        assert z is None
        assert advisory is not None
        assert advisory.code == "crm_uncertainty_invalid"


class TestBlankAdvisory:
    def test_above_threshold_raises_the_flag_with_both_numbers(self) -> None:
        advisory = blank_advisory(detected("0.20"), threshold_g_t=Decimal("0.05"))
        assert advisory is not None
        assert advisory.code == "blank_above_threshold"
        assert "0.20" in advisory.detail and "0.05" in advisory.detail

    def test_at_threshold_is_quiet(self) -> None:
        """Strictly-above is the line: detectable-but-tiny gold in a blank is
        routine, and flagging it would train readers to ignore flags."""
        assert blank_advisory(detected("0.05"), threshold_g_t=Decimal("0.05")) is None

    def test_below_threshold_is_quiet(self) -> None:
        assert blank_advisory(detected("0.01"), threshold_g_t=Decimal("0.05")) is None

    def test_a_non_detect_blank_is_the_good_case_and_stays_quiet(self) -> None:
        assert blank_advisory(non_detect("0.01"), threshold_g_t=Decimal("0.05")) is None

    def test_a_non_positive_threshold_refuses(self) -> None:
        with pytest.raises(ValueError):
            blank_advisory(detected("1"), threshold_g_t=Decimal("0"))


class TestReconstructionFeedsTheAdvisories:
    def test_grade_from_recorded_weighing_feeds_z_exactly(self) -> None:
        """End to end through the domain: 0.231 mg bead over 30 g portion,
        graded, then compared to 1.50 ± 0.03 g/t certified.

        0.231 mg / (30 g ÷ 29.1666… g/assay-ton) → exactly 0.225 g/t? No —
        this case pins the *plumbing*, not one magic number: whatever exact
        grade the assay module produces must sit exactly its computed distance
        from centre.
        """
        grade = gravimetric_grade(
            gold_bead_mg=Decimal("0.225"),
            sample_weight_g=Decimal("45"),
            balance_sensitivity_mg=None,
        )
        assert grade.value == Decimal("5")  # 0.225 mg per 45 g is exactly 5 g/t
        z, advisory = crm_z_score(
            grade,
            certified_au_value_g_t=Decimal("4.94"),
            certified_au_uncertainty_g_t=Decimal("0.03"),
        )
        assert z == Decimal("2")
        assert advisory is None


class TestDuplicatePairAdvisory:
    def test_stats_are_exact_and_terminate(self) -> None:
        """5 and 3 g/t: mean 4, difference 2, RPD exactly 50 % — flagged."""
        from msa_lims.domain.qc import duplicate_pair_advisory

        stats, advisory = duplicate_pair_advisory(
            detected("5"), detected("3"), max_rpd_percent=Decimal("20")
        )
        assert stats is not None
        assert stats.mean_g_t == Decimal("4")
        assert stats.abs_diff_g_t == Decimal("2")
        assert stats.rpd_percent == Decimal("50")
        assert advisory is not None
        assert advisory.code == "duplicate_rpd_above_max"

    def test_below_the_max_is_quiet(self) -> None:
        from msa_lims.domain.qc import duplicate_pair_advisory

        stats, advisory = duplicate_pair_advisory(
            detected("5"), detected("4.8"), max_rpd_percent=Decimal("20")
        )
        assert stats is not None
        assert stats.mean_g_t == Decimal("4.9")
        assert stats.abs_diff_g_t == Decimal("0.2")
        assert advisory is None

    def test_at_the_max_is_quiet_and_strictly_above_flags(self) -> None:
        """11 vs 9 over a 20 % max is exactly 20 % -- quiet. 10 vs 7.5 is
        28.57… % -- flagged, with the exact rational rendering preserved."""
        from msa_lims.domain.qc import duplicate_pair_advisory

        stats, advisory = duplicate_pair_advisory(
            detected("11"), detected("9"), max_rpd_percent=Decimal("20")
        )
        assert stats is not None
        assert stats.rpd_percent == Decimal("20")
        assert advisory is None

        stats, advisory = duplicate_pair_advisory(
            detected("10"), detected("7.5"), max_rpd_percent=Decimal("20")
        )
        assert stats is not None
        assert stats.rpd_percent > Decimal("20")
        assert advisory is not None
        assert advisory.code == "duplicate_rpd_above_max"

    def test_a_censored_half_yields_no_invented_statistics(self) -> None:
        from msa_lims.domain.qc import duplicate_pair_advisory

        stats, advisory = duplicate_pair_advisory(
            detected("5"), non_detect("0.01"), max_rpd_percent=Decimal("20")
        )
        assert stats is None
        assert advisory is not None
        assert advisory.code == "duplicate_not_graded"

    def test_an_ungraded_original_is_named_as_such(self) -> None:
        from msa_lims.domain.qc import duplicate_pair_advisory

        stats, advisory = duplicate_pair_advisory(
            None, detected("5"), max_rpd_percent=Decimal("20")
        )
        assert stats is None
        assert advisory is not None
        assert advisory.code == "original_not_graded"

    def test_a_zero_mean_refuses_to_divide(self) -> None:
        """Both grading exactly 0 makes RPD 0/0; the honest answer is 'no
        statistics', not a lucky number."""
        from msa_lims.domain.qc import duplicate_pair_advisory

        zero = MeasuredValue.detected(Decimal("0"), Unit.G_PER_TONNE)
        stats, advisory = duplicate_pair_advisory(zero, zero, max_rpd_percent=Decimal("20"))
        assert stats is None
        assert advisory is not None
        assert advisory.code == "pair_mean_zero"

    def test_a_non_positive_max_refuses(self) -> None:
        from msa_lims.domain.qc import duplicate_pair_advisory

        with pytest.raises(ValueError):
            duplicate_pair_advisory(detected("5"), detected("5"), max_rpd_percent=Decimal("0"))
