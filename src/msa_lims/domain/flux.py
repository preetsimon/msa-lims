"""Flux charge scaling: from a recipe to what a technician actually weighs out.

A flux recipe's reagent masses are calibrated against a nominal portion — the
sample weight the formula was written for. A crucible rarely gets exactly
that weight, so the amounts to weigh out are the recipe scaled by the ratio
of the actual portion to the nominal one. This is the same discipline as
:mod:`msa_lims.domain.assay`: pure Decimal arithmetic, a pinned working
precision, no session, no clock — a scaling can be re-derived from a stored
crucible row years later and match bit for bit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from msa_lims.domain.units import CONVERSION_PRECISION


class FluxCalculationError(ValueError):
    """The inputs to a flux scaling are not physically possible."""


@dataclass(frozen=True, slots=True)
class FluxAmounts:
    """Reagent masses in grams. Any may be zero — not every recipe uses every
    reagent — but none may be negative."""

    litharge_g: Decimal
    soda_ash_g: Decimal
    borax_g: Decimal
    silica_g: Decimal
    flour_g: Decimal
    nitre_g: Decimal


def scale_flux_charge(
    nominal: FluxAmounts, *, nominal_portion_g: Decimal, sample_weight_g: Decimal
) -> FluxAmounts:
    """The reagent masses to weigh out for an actual sample weight.

    Scaling is linear: doubling the sample weight doubles every reagent. That
    is the entire physical premise of a flux recipe — it specifies
    proportions, not absolutes — so a single ratio applies to all six
    columns identically.
    """
    _require_positive(nominal_portion_g, "nominal portion")
    _require_positive(sample_weight_g, "sample weight")

    with localcontext() as ctx:
        ctx.prec = CONVERSION_PRECISION
        factor = sample_weight_g / nominal_portion_g
        return FluxAmounts(
            litharge_g=nominal.litharge_g * factor,
            soda_ash_g=nominal.soda_ash_g * factor,
            borax_g=nominal.borax_g * factor,
            silica_g=nominal.silica_g * factor,
            flour_g=nominal.flour_g * factor,
            nitre_g=nominal.nitre_g * factor,
        )


def _require_positive(value: Decimal, name: str) -> None:
    if value <= 0:
        raise FluxCalculationError(f"{name} must be greater than zero, got {value}")


__all__ = ["FluxAmounts", "FluxCalculationError", "scale_flux_charge"]
