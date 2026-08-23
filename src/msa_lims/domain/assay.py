"""Fire assay arithmetic: from a weighed bead to a reported grade.

This is the calculation the whole laboratory exists to perform, and it is four
lines of division surrounded by the reasons it goes wrong.

**The physical process.** A weighed portion of pulp is fused with flux; the
precious metals collect in a lead button. Cupellation oxidises the lead away and
leaves a *doré bead* — gold **and silver** together. Parting the bead in nitric
acid dissolves the silver, leaving gold. So there are two bead weights, and they
mean different things:

* ``dore_bead_mg`` — Au + Ag, weighed after cupellation, before parting.
* ``gold_bead_mg`` — Au alone, weighed after parting.

Reporting the doré weight as gold is the classic error in this calculation, and
it inflates the grade by however much silver the sample carried. The two are
separate parameters here with no default between them, so the mistake has to be
typed deliberately rather than made by omission.

**The assay ton.** Fire assay's traditional sample weight, 29.1666… g, is not
arbitrary: it is exactly 175/6 g, chosen so that a bead weighing one milligram
comes from material grading exactly one troy ounce per short ton. The convention
predates the metric grade report and survives in the balances and the glassware,
so it is encoded here rather than rounded into a comment.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from msa_lims.domain.units import CONVERSION_PRECISION, Unit, convert
from msa_lims.domain.values import MeasuredValue

#: One assay ton in grams, exactly 175/6. Held as a rational for the same reason
#: the unit factors are: 29.1666666667 is not the number, and a year of assays
#: divided by a truncated constant drifts.
ASSAY_TON_NUMERATOR = Decimal(175)
ASSAY_TON_DENOMINATOR = Decimal(6)

#: Milligrams of bead per gram of sample, expressed as grams per tonne. One
#: milligram in one gram is one part per thousand, which is 1000 g/t.
_MG_PER_G_AS_G_PER_TONNE = Decimal(1000)


class AssayCalculationError(ValueError):
    """The inputs to a grade calculation are not physically possible."""


def assay_ton_grams() -> Decimal:
    """One assay ton in grams, to full working precision."""
    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        return ASSAY_TON_NUMERATOR / ASSAY_TON_DENOMINATOR


def gravimetric_grade(
    *,
    gold_bead_mg: Decimal,
    sample_weight_g: Decimal,
    balance_sensitivity_mg: Decimal | None = None,
) -> MeasuredValue:
    """Grade in g/t from a parted gold bead and the portion it came from.

    ``gold_bead_mg`` is the bead **after parting** — gold alone. Pass the doré
    weight here and every result is high by the silver content.

    When ``balance_sensitivity_mg`` is given and the bead is at or below it, the
    result is returned as a non-detect at the grade that sensitivity corresponds
    to, rather than as a very small number. A bead the balance cannot resolve
    from zero is not a measurement of a small amount of gold; it is the absence
    of a measurement, and :class:`~msa_lims.domain.values.MeasuredValue` is
    where that distinction is kept honest all the way to the certificate.
    """
    _require_positive(sample_weight_g, "sample weight")
    if gold_bead_mg < 0:
        raise AssayCalculationError(f"bead weight cannot be negative: {gold_bead_mg} mg")

    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        if balance_sensitivity_mg is not None:
            _require_positive(balance_sensitivity_mg, "balance sensitivity")
            if gold_bead_mg <= balance_sensitivity_mg:
                limit = balance_sensitivity_mg * _MG_PER_G_AS_G_PER_TONNE / sample_weight_g
                return MeasuredValue.non_detect(limit, Unit.G_PER_TONNE)

        grade = gold_bead_mg * _MG_PER_G_AS_G_PER_TONNE / sample_weight_g
        detection_limit = (
            None
            if balance_sensitivity_mg is None
            else balance_sensitivity_mg * _MG_PER_G_AS_G_PER_TONNE / sample_weight_g
        )
        return MeasuredValue.detected(grade, Unit.G_PER_TONNE, detection_limit)


def silver_by_difference(
    *,
    dore_bead_mg: Decimal,
    gold_bead_mg: Decimal,
    sample_weight_g: Decimal,
) -> MeasuredValue:
    """Silver grade in g/t, taken as what parting removed from the bead.

    This is a difference of two weighings, so its uncertainty is the sum of
    theirs — silver by difference is a weaker number than gold by direct weight,
    and a certificate that reports both should say which is which.

    A gold bead heavier than the doré bead it was parted from is refused. It
    means the two weights were transposed, or one belongs to another crucible,
    and the negative silver grade it would otherwise produce is the kind of
    value that gets rounded to zero somewhere downstream and never investigated.
    """
    _require_positive(sample_weight_g, "sample weight")
    if gold_bead_mg > dore_bead_mg:
        raise AssayCalculationError(
            f"parted gold bead ({gold_bead_mg} mg) is heavier than the doré bead it came "
            f"from ({dore_bead_mg} mg); the weights are transposed or mismatched"
        )

    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        silver_mg = dore_bead_mg - gold_bead_mg
        grade = silver_mg * _MG_PER_G_AS_G_PER_TONNE / sample_weight_g
        return MeasuredValue.detected(grade, Unit.G_PER_TONNE)


def grade_in_ounces_per_ton(grade: MeasuredValue) -> MeasuredValue:
    """The same grade in troy ounces per short ton, for a North American client.

    A convenience over :func:`~msa_lims.domain.units.convert`, kept here because
    it is the one conversion clients ask for by name.
    """
    return grade.converted_to(Unit.OZ_PER_TON)


def bead_weight_for_grade(*, grade_g_per_tonne: Decimal, sample_weight_g: Decimal) -> Decimal:
    """The bead a given grade would produce — the inverse calculation.

    Used when charging a batch: it answers "will this bead be weighable, and
    will it be too big for the cupel?" before the furnace runs, and it is what
    lets a high-grade sample be assigned a smaller portion in advance.
    """
    _require_positive(sample_weight_g, "sample weight")
    if grade_g_per_tonne < 0:
        raise AssayCalculationError(f"grade cannot be negative: {grade_g_per_tonne}")
    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        return grade_g_per_tonne * sample_weight_g / _MG_PER_G_AS_G_PER_TONNE


def _require_positive(value: Decimal, name: str) -> None:
    if value <= 0:
        raise AssayCalculationError(f"{name} must be greater than zero, got {value}")


# Re-exported so callers converting grades do not have to import two modules.
__all__ = [
    "ASSAY_TON_DENOMINATOR",
    "ASSAY_TON_NUMERATOR",
    "AssayCalculationError",
    "assay_ton_grams",
    "bead_weight_for_grade",
    "convert",
    "grade_in_ounces_per_ton",
    "gravimetric_grade",
    "silver_by_difference",
]
