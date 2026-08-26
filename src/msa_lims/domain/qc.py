"""Advisory QC arithmetic over sealed dossier data.

**Nothing here judges.** The module computes the two numbers a geologist's
QA/QC review asks for — how far a CRM's measured grade sits from its
certified value in certified-uncertainty units, and whether a blank came
back above the lab's contamination threshold — and returns them with flags
describing what was computed. Pass/fail verdicts, control charts, warning
limits: those are QC Sentinel's job on export (see
``domain/enums.py``'s ``QcMaterialType`` docstring), and there is
deliberately no verdict vocabulary here to express them with.

Every function is pure — no session, no clock, pinned decimal context —
and every input arrives as it is stored: grades reconstructed by
:func:`msa_lims.domain.assay.gravimetric_grade`, certified values exactly
as ``qc_material`` holds them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from msa_lims.domain.units import CONVERSION_PRECISION
from msa_lims.domain.values import MeasuredValue


@dataclass(frozen=True, slots=True)
class Advisory:
    """One advisory observation about one QC insertion — never a verdict.

    ``code`` is a stable machine-readable key (an export to Sentinel can
    carry it); ``detail`` is written for the person reading the dossier.
    """

    code: str
    detail: str


def crm_z_score(
    measured: MeasuredValue,
    *,
    certified_au_value_g_t: Decimal,
    certified_au_uncertainty_g_t: Decimal,
) -> tuple[Decimal | None, Advisory | None]:
    """How many certified uncertainties the measured grade sits from centre.

    ``(measured − certified) / uncertainty``, the number every CRM review
    starts from — ±2 as the usual action line is *Sentinel's* policy to
    apply, not this function's. Returns ``None`` and an advisory instead
    when the measurement cannot be compared at all:

    * a **non-detect** on a CRM means the fusion produced no measurable gold
      where the certificate guarantees some — comparable to nothing, so no z
      is invented;
    * a non-positive uncertainty would make z meaningless; the registration
      path refuses one anyway (a CHECK constraint agrees), so reaching this
      branch means the row predates that rule or was written directly.
    """
    if certified_au_uncertainty_g_t <= 0:
        return None, Advisory(
            "crm_uncertainty_invalid",
            f"certified uncertainty {certified_au_uncertainty_g_t} g/t is not positive; "
            "no z-score is meaningful",
        )
    if not measured.is_detected:
        return None, Advisory(
            "crm_non_detect",
            "the CRM came back non-detect; there is no measured grade to compare "
            "against its certified value",
        )
    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        z = (measured.require_detected("the CRM z-score") - certified_au_value_g_t) / (
            certified_au_uncertainty_g_t
        )
        return z, None


def blank_advisory(measured: MeasuredValue, *, threshold_g_t: Decimal) -> Advisory | None:
    """Whether a blank's grade clears the lab's contamination threshold.

    A blank's whole point is that nothing should come back, so a non-detect
    — the best possible outcome — raises nothing. A detected grade *above*
    the threshold raises ``blank_above_threshold``; a detected grade at or
    below it is quiet: detectable-but-tiny gold in a blank is routine balance
    territory, and calling it out would train readers to ignore the flag.
    """
    if threshold_g_t <= 0:
        raise ValueError(f"blank threshold must be positive, got {threshold_g_t}")
    if not measured.is_detected:
        return None
    value = measured.require_detected("the blank comparison")
    if value > threshold_g_t:
        return Advisory(
            "blank_above_threshold",
            f"blank graded {value} g/t, above the {threshold_g_t} g/t contamination threshold",
        )
    return None


@dataclass(frozen=True, slots=True)
class DuplicatePairStats:
    """What one original/duplicate crucible pair says about precision.

    ``rpd_percent`` is the relative percent difference the industry standard
    reports for duplicate pairs (Abzalov 2008's formulation alongside
    Thompson–Howarth's): |dup − orig| ÷ mean × 100. The pair's mean and
    absolute difference are also returned as the canonical (x, y) point of a
    Thompson–Howarth plot, so an export can feed the chart directly without
    recomputation.
    """

    mean_g_t: Decimal
    abs_diff_g_t: Decimal
    rpd_percent: Decimal


def duplicate_pair_advisory(
    original: MeasuredValue | None,
    duplicate: MeasuredValue | None,
    *,
    max_rpd_percent: Decimal,
) -> tuple[DuplicatePairStats | None, Advisory | None]:
    """Compare one duplicate against its original, advisories only.

    Either side being ungraded (``None`` — unweighed, or a weight the domain
    refused to grade blind) or non-detect yields no statistics and names why:
    a censored half makes RPD meaningless, and inventing a number from the
    censoring limit would be manufacturing data. A zero mean (both sides
    grading exactly 0) divides by nothing — refused the same way.

    An RPD strictly above ``max_rpd_percent`` raises
    ``duplicate_rpd_above_max``; at or below is quiet, for the same reason a
    blank at threshold stays quiet.
    """
    if max_rpd_percent <= 0:
        raise ValueError(f"max RPD must be positive, got {max_rpd_percent}")
    if original is None or not original.is_detected:
        return None, Advisory(
            "original_not_graded",
            "the original has no detected grade in this batch; there is nothing "
            "to compare the duplicate against",
        )
    if duplicate is None or not duplicate.is_detected:
        return None, Advisory(
            "duplicate_not_graded",
            "the duplicate has no detected grade on record yet",
        )
    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        o = original.require_detected("the duplicate comparison")
        d = duplicate.require_detected("the duplicate comparison")
        mean = (o + d) / 2
        if mean == 0:
            return None, Advisory(
                "pair_mean_zero",
                "both grades are exactly zero; a relative difference is undefined",
            )
        abs_diff = abs(d - o)
        rpd = abs_diff / mean * 100
        stats = DuplicatePairStats(mean_g_t=mean, abs_diff_g_t=abs_diff, rpd_percent=rpd)
    if rpd > max_rpd_percent:
        return stats, Advisory(
            "duplicate_rpd_above_max",
            f"duplicate differs by {rpd} % from its original, above the "
            f"{max_rpd_percent} % maximum",
        )
    return stats, None
