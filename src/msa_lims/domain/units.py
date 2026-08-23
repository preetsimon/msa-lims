"""Units of measure and explicit conversion between them.

Ported from QC Sentinel's ``domain/units.py``, which solved this problem
correctly, and extended with a **mass** dimension. Sentinel only ever compares
concentrations; a LIMS additionally weighs things — sample received weight, pulp
weight, the lead button, the doré bead — and those weights are what grade is
calculated *from*, so they need the same exactness.

Two rules hold everywhere in this package:

1. Arithmetic is ``Decimal``, never ``float``. A certificate must be
   reproducible bit-for-bit years later, and binary floats are not reproducible
   across platforms in the last digits.
2. Conversion is explicit. Comparing two values in different units without an
   intervening conversion raises :class:`IncompatibleUnitsError` rather than
   producing a plausible wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum

# Conversion runs inside a pinned context so a result never depends on whatever
# precision the calling process happened to be using.
CONVERSION_PRECISION = 34


class UnitError(ValueError):
    """Base class for unit problems."""


class UnknownUnitError(UnitError):
    """The unit string in an export or a form is not one we recognise."""


class IncompatibleUnitsError(UnitError):
    """Conversion was requested between units of different physical dimensions."""


class Dimension(Enum):
    """What kind of quantity a unit measures.

    Conversion within a dimension is arithmetic. Conversion across dimensions
    needs physical information we do not have (density, aliquot volume, or in
    the mass/mass-fraction case the sample weight itself), so it is refused
    instead of guessed. Turning a bead weight into a grade is a *calculation*
    with a named input, not a unit conversion — see :mod:`msa_lims.domain.assay`.
    """

    MASS_FRACTION = "mass_fraction"
    MASS_CONCENTRATION = "mass_concentration"
    MASS = "mass"


class Unit(Enum):
    PPM = "ppm"
    PPB = "ppb"
    PERCENT = "%"
    G_PER_TONNE = "g/t"
    OZ_PER_TON = "oz/t"
    MG_PER_L = "mg/L"
    UG_PER_L = "ug/L"
    # Mass. Sample and pulp weights are reported in grams; lead buttons and
    # doré beads in milligrams, because a bead is often under a milligram and
    # writing it in grams buries the significant digits behind zeros.
    GRAM = "g"
    MILLIGRAM = "mg"
    MICROGRAM = "ug"
    KILOGRAM = "kg"


@dataclass(frozen=True, slots=True)
class _UnitSpec:
    """How a unit relates to the canonical unit of its dimension.

    The factor is held as an exact rational (``numerator / denominator``) rather
    than a pre-divided decimal because some conversions do not terminate. One
    troy ounce per short ton is exactly 240/7 g/t; storing ``34.2857142857`` and
    calling it exact would be a lie that compounds over a year of data.
    """

    dimension: Dimension
    numerator: Decimal
    denominator: Decimal


# Canonical unit is ppm for mass fraction, mg/L for mass concentration, and the
# gram for mass. g/t and ppm are the same quantity (one gram in one tonne is one
# part per million by mass); both are kept because labs report in the one their
# clients expect and round-tripping the label matters for the audit trail.
_SPECS: dict[Unit, _UnitSpec] = {
    Unit.PPM: _UnitSpec(Dimension.MASS_FRACTION, Decimal(1), Decimal(1)),
    Unit.G_PER_TONNE: _UnitSpec(Dimension.MASS_FRACTION, Decimal(1), Decimal(1)),
    Unit.PPB: _UnitSpec(Dimension.MASS_FRACTION, Decimal(1), Decimal(1000)),
    Unit.PERCENT: _UnitSpec(Dimension.MASS_FRACTION, Decimal(10_000), Decimal(1)),
    Unit.OZ_PER_TON: _UnitSpec(Dimension.MASS_FRACTION, Decimal(240), Decimal(7)),
    Unit.MG_PER_L: _UnitSpec(Dimension.MASS_CONCENTRATION, Decimal(1), Decimal(1)),
    Unit.UG_PER_L: _UnitSpec(Dimension.MASS_CONCENTRATION, Decimal(1), Decimal(1000)),
    Unit.GRAM: _UnitSpec(Dimension.MASS, Decimal(1), Decimal(1)),
    Unit.MILLIGRAM: _UnitSpec(Dimension.MASS, Decimal(1), Decimal(1000)),
    Unit.MICROGRAM: _UnitSpec(Dimension.MASS, Decimal(1), Decimal(1_000_000)),
    Unit.KILOGRAM: _UnitSpec(Dimension.MASS, Decimal(1000), Decimal(1)),
}

# Spellings seen in instrument exports and on data-entry forms, lowercased.
# Instruments are inconsistent about case and about micro signs, so normalise on
# the way in rather than scattering `.lower()` calls through the callers.
_ALIASES: dict[str, Unit] = {
    "ppm": Unit.PPM,
    "mg/kg": Unit.PPM,
    "ppb": Unit.PPB,
    "ug/kg": Unit.PPB,
    "µg/kg": Unit.PPB,
    "%": Unit.PERCENT,
    "pct": Unit.PERCENT,
    "percent": Unit.PERCENT,
    "g/t": Unit.G_PER_TONNE,
    "gpt": Unit.G_PER_TONNE,
    "g/tonne": Unit.G_PER_TONNE,
    "oz/t": Unit.OZ_PER_TON,
    "opt": Unit.OZ_PER_TON,
    "oz/ton": Unit.OZ_PER_TON,
    "mg/l": Unit.MG_PER_L,
    "ug/l": Unit.UG_PER_L,
    "µg/l": Unit.UG_PER_L,
    "g": Unit.GRAM,
    "gram": Unit.GRAM,
    "grams": Unit.GRAM,
    "mg": Unit.MILLIGRAM,
    "milligram": Unit.MILLIGRAM,
    "ug": Unit.MICROGRAM,
    "µg": Unit.MICROGRAM,
    "kg": Unit.KILOGRAM,
}


def dimension_of(unit: Unit) -> Dimension:
    return _SPECS[unit].dimension


def parse_unit(text: str) -> Unit:
    """Map a unit string to a :class:`Unit`.

    Raises :class:`UnknownUnitError` for anything unrecognised. Unknown units
    reject the row: a wrong unit is a wrong grade on a certificate, and a wrong
    grade that looks right is worse than an import that stopped and said why.
    """
    key = text.strip().lower()
    try:
        return _ALIASES[key]
    except KeyError:
        raise UnknownUnitError(f"unrecognised unit {text!r}") from None


def convert(value: Decimal, source: Unit, target: Unit) -> Decimal:
    """Convert ``value`` from ``source`` to ``target`` units.

    Raises :class:`IncompatibleUnitsError` when the units measure different
    dimensions. Rounding is ROUND_HALF_EVEN at :data:`CONVERSION_PRECISION`
    significant digits, fixed here so the same inputs always give the same
    output regardless of caller context.

    Conversion is deterministic but not always invertible. Converting to a unit
    whose factor does not terminate in base 10 — oz/t, at 240/7 — and back can
    differ from the original in the last digit. This is a property of decimal
    arithmetic, not a defect, and it is why measurements are persisted in the
    unit they were reported in. Conversion happens when values are compared or
    printed, never as a storage step, so no stored number is ever the result of
    a round trip.
    """
    if source is target:
        return value

    src, dst = _SPECS[source], _SPECS[target]
    if src.dimension is not dst.dimension:
        raise IncompatibleUnitsError(
            f"cannot convert {source.value} ({src.dimension.value}) to "
            f"{target.value} ({dst.dimension.value}) without physical assumptions"
        )

    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        canonical = value * src.numerator / src.denominator
        return canonical * dst.denominator / dst.numerator
