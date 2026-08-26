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

**Two finishes, one fusion.** Everything above describes the *gravimetric*
finish, where the parted bead is weighed directly. The other route dissolves
that bead in aqua regia, makes it up to a known volume, and reads the gold
concentration of that solution against a calibration curve on an AAS or an
ICP-MS. The fusion is identical; only the last step differs, which is why both
finishes produce a :class:`~msa_lims.domain.values.MeasuredValue` in g/t from
this one module and why both are ``FIRE_ASSAY_*`` members of
:class:`~msa_lims.domain.enums.AssayMethod`.

The two are not interchangeable, and the direction of the trade matters:
weighing is the referee method and does not saturate, while a solution finish
is faster and reads far lower but runs off the top of its calibration curve on
high-grade material. That is the whole reason a lab keeps both — and why
:func:`solution_finish_grade` refuses a reading above the calibration range it
was given rather than reporting a saturated number as a grade.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from msa_lims.domain.units import CONVERSION_PRECISION, Dimension, Unit, convert, dimension_of
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
    if gold_bead_mg == 0 and balance_sensitivity_mg is None:
        # A zero reading with no stated sensitivity cannot be told apart from
        # one below what the balance can resolve — and reporting it as a
        # detected 0 g/t would flatten exactly the distinction
        # MeasuredValue exists to keep. Stating the sensitivity turns the same
        # reading into a non-detect at the grade that sensitivity corresponds
        # to, which is the honest shape of "we weighed nothing".
        raise AssayCalculationError(
            "a bead weighing zero requires balance_sensitivity_mg to be stated: "
            "without it the reading cannot be distinguished from one below "
            "detection, so no grade can be reported"
        )

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


def solution_finish_grade(
    *,
    concentration: Decimal,
    concentration_unit: Unit,
    solution_volume_ml: Decimal,
    sample_weight_g: Decimal,
    detection_limit: Decimal | None = None,
    upper_calibration_limit: Decimal | None = None,
) -> MeasuredValue:
    """Grade in g/t from a dissolved bead read on an AAS or ICP-MS.

    The bead from a normal fusion is dissolved and made up to
    ``solution_volume_ml``; the instrument reports how concentrated that
    solution is. All the gold in the portion is in that flask, so the grade
    falls out of one multiplication and one division::

        µg/mL × mL = µg of gold in the flask
        µg of gold ÷ g of sample = µg/g = g/t

    ``detection_limit`` and ``upper_calibration_limit`` are the *method's*
    limits, expressed in ``concentration_unit`` like the reading itself —
    they are properties of the calibration the instrument was run against,
    not of this code, so they are passed in rather than assumed. Both are
    optional and neither is guessed when absent.

    A reading at or below the detection limit comes back as a non-detect at
    the grade that limit corresponds to, exactly as
    :func:`gravimetric_grade` treats a bead below the balance's sensitivity.

    A reading **above** ``upper_calibration_limit`` is refused outright. Past
    the top standard the instrument is extrapolating off the end of its curve,
    and the number it prints is not a measurement of anything — reporting it
    as a grade would put the one value a solution finish is known to get wrong
    onto a certificate. The refusal names the two things a lab actually does
    about it: dilute the solution and read it again, or finish the sample
    gravimetrically, which is the referee method precisely because it has no
    ceiling.

    ``concentration_unit`` must measure mass *concentration* (mg/L, µg/L) —
    how much gold is in a volume of liquid. A mass-*fraction* unit like ppm
    describes a solid and is refused, because "ppm" on an instrument printout
    is ambiguous between µg/mL of solution and µg/g of sample, and the two
    differ by exactly the factor this function exists to apply.
    """
    _require_positive(sample_weight_g, "sample weight")
    _require_positive(solution_volume_ml, "solution volume")
    if concentration < 0:
        raise AssayCalculationError(f"concentration cannot be negative: {concentration}")
    if dimension_of(concentration_unit) is not Dimension.MASS_CONCENTRATION:
        raise AssayCalculationError(
            f"{concentration_unit.value} measures "
            f"{dimension_of(concentration_unit).value}, but a solution finish reads a mass "
            "concentration (mg/L, ug/L); a solid's units cannot describe what is in the flask"
        )

    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        # mg/L is canonical for this dimension, and mg/L is numerically µg/mL —
        # which is what makes the grade formula below a bare multiply-divide
        # with no further factor to get wrong.
        mg_per_l = convert(concentration, concentration_unit, Unit.MG_PER_L)

        if upper_calibration_limit is not None:
            _require_positive(upper_calibration_limit, "upper calibration limit")
            upper_mg_per_l = convert(upper_calibration_limit, concentration_unit, Unit.MG_PER_L)
            if mg_per_l > upper_mg_per_l:
                raise AssayCalculationError(
                    f"reading of {concentration} {concentration_unit.value} is above the "
                    f"method's calibration range ({upper_calibration_limit} "
                    f"{concentration_unit.value}); the instrument is extrapolating past its "
                    "top standard, so this is not a measurement. Dilute and re-read, or "
                    "finish this sample gravimetrically"
                )

        def as_grade(value_mg_per_l: Decimal) -> Decimal:
            return value_mg_per_l * solution_volume_ml / sample_weight_g

        if detection_limit is not None:
            _require_positive(detection_limit, "detection limit")
            limit_mg_per_l = convert(detection_limit, concentration_unit, Unit.MG_PER_L)
            grade_detection_limit = as_grade(limit_mg_per_l)
            if mg_per_l <= limit_mg_per_l:
                return MeasuredValue.non_detect(grade_detection_limit, Unit.G_PER_TONNE)
        else:
            grade_detection_limit = None

        if detection_limit is None and concentration == 0:
            # Same reasoning as a zero bead with no stated balance sensitivity:
            # a reading of zero with no detection limit behind it cannot be
            # told apart from one below what the method can see, and calling it
            # a detected 0 g/t flattens exactly the distinction MeasuredValue
            # exists to keep.
            raise AssayCalculationError(
                "a concentration of zero requires detection_limit to be stated: without it "
                "the reading cannot be distinguished from one below the method's detection "
                "limit, so no grade can be reported"
            )

        return MeasuredValue.detected(as_grade(mg_per_l), Unit.G_PER_TONNE, grade_detection_limit)


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
    "solution_finish_grade",
]
