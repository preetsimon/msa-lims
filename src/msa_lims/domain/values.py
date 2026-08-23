"""Measured values, including results reported below the detection limit.

Ported from QC Sentinel's ``domain/values.py``. The argument for it is, if
anything, stronger in a LIMS than in a QC monitor: this system *issues the
certificate*. A ``<0.01`` that silently became ``0`` somewhere between the
instrument and the PDF is a number a client will drill on.

A lab result is not a number. It is either a detected quantity or the statement
"whatever this is, it is below what this method can see" — a result of
``<0.01 g/t`` says nothing about whether the true value is 0.009 or 0.0.

Most systems flatten that distinction on the way in, substituting zero or half
the detection limit so the arithmetic keeps working. The substitution is then
invisible, and every downstream mean and composite grade silently inherits it.

:class:`MeasuredValue` refuses to flatten. It has no ``__float__``, no
``__int__``, and no ordering against plain numbers, so a non-detect cannot drift
into a calculation by accident. Code that needs a number must either assert the
value was detected (:meth:`MeasuredValue.require_detected`) or name the
substitution it wants (:meth:`MeasuredValue.substituted`), and that choice is
then visible in the diff and on the certificate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

from msa_lims.domain.units import Unit, convert


class ValueParseError(ValueError):
    """A result token from an export or a form could not be read as a value."""


class CensoredValueError(ValueError):
    """A non-detect was used where a definite number was required."""


class Substitution(Enum):
    """Named conventions for turning a non-detect into a number.

    Which one is correct is a client and method policy question, not a code
    question, so the choice is recorded alongside whatever used it rather than
    being a default buried in a helper.
    """

    ZERO = "zero"
    HALF_DETECTION_LIMIT = "half_dl"
    DETECTION_LIMIT = "dl"


# "<0.01", "< 0.01", "<=0.01" — the leading operator is what marks a non-detect.
_NON_DETECT = re.compile(r"^<=?\s*(?P<limit>[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?)$")
_NUMBER = re.compile(r"^[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$")


@dataclass(frozen=True, slots=True)
class MeasuredValue:
    """A single analytical result.

    Exactly one of two shapes: detected, where :attr:`value` is the measurement;
    or censored, where :attr:`value` is ``None`` and :attr:`detection_limit`
    holds the limit in force when the measurement was made.

    The detection limit is stored on the value rather than looked up later
    because limits change when a method is revalidated. Reissuing a 2024
    certificate requires the 2024 limit, so it travels with the measurement.
    """

    unit: Unit
    value: Decimal | None
    detection_limit: Decimal | None
    censored: bool

    def __post_init__(self) -> None:
        if self.censored:
            if self.value is not None:
                raise ValueError("a censored value cannot carry a measured value")
            if self.detection_limit is None:
                raise ValueError("a censored value requires a detection limit")
        elif self.value is None:
            raise ValueError("a detected value requires a measurement")

    # -- constructors -------------------------------------------------------

    @classmethod
    def detected(
        cls, value: Decimal, unit: Unit, detection_limit: Decimal | None = None
    ) -> MeasuredValue:
        return cls(unit=unit, value=value, detection_limit=detection_limit, censored=False)

    @classmethod
    def non_detect(cls, detection_limit: Decimal, unit: Unit) -> MeasuredValue:
        return cls(unit=unit, value=None, detection_limit=detection_limit, censored=True)

    @classmethod
    def parse(cls, token: str, unit: Unit) -> MeasuredValue:
        """Read a result token as written in an instrument export.

        ``"1.42"`` is detected; ``"<0.01"`` is a non-detect whose reported limit
        is 0.01. A bare ``"ND"`` carries no limit, so it cannot be turned into a
        usable value and is rejected — the row goes to quarantine with a reason
        rather than into the data with a guess.
        """
        text = token.strip()
        if not text:
            raise ValueParseError("empty result")

        if match := _NON_DETECT.match(text):
            return cls.non_detect(_decimal(match.group("limit")), unit)
        if _NUMBER.match(text):
            return cls.detected(_decimal(text), unit)
        raise ValueParseError(
            f"cannot read {token!r} as a result; non-detects must carry their limit, as in '<0.01'"
        )

    # -- accessors ----------------------------------------------------------

    @property
    def is_detected(self) -> bool:
        return not self.censored

    def require_detected(self, context: str = "this calculation") -> Decimal:
        """Return the measured value, or raise if it is a non-detect.

        Call this from calculations that have no defensible behaviour on
        censored data — a composite grade over a drilled interval, for instance.
        Raising surfaces the ambiguity to the analyst instead of resolving it
        with an assumption nobody chose.
        """
        if self.censored:
            raise CensoredValueError(
                f"{context} needs a definite value but the result is "
                f"<{self.detection_limit} {self.unit.value}; "
                "handle the non-detect explicitly or exclude it"
            )
        assert self.value is not None  # guaranteed by __post_init__
        return self.value

    def substituted(self, strategy: Substitution) -> Decimal:
        """Return a number, naming the convention used for non-detects.

        Detected values are returned unchanged; the strategy applies only to
        censored ones. The caller records which strategy it used so the
        substitution appears wherever the derived number is shown.
        """
        if self.is_detected:
            assert self.value is not None
            return self.value

        assert self.detection_limit is not None
        match strategy:
            case Substitution.ZERO:
                return Decimal(0)
            case Substitution.HALF_DETECTION_LIMIT:
                return self.detection_limit / Decimal(2)
            case Substitution.DETECTION_LIMIT:
                return self.detection_limit

    def converted_to(self, unit: Unit) -> MeasuredValue:
        """Return the same result expressed in ``unit``.

        The detection limit converts with the value; a limit left in the old
        unit is the kind of error that reads as a real analytical finding.
        """
        if unit is self.unit:
            return self
        limit = (
            None if self.detection_limit is None else convert(self.detection_limit, self.unit, unit)
        )
        if self.censored:
            assert limit is not None
            return MeasuredValue.non_detect(limit, unit)
        assert self.value is not None
        return MeasuredValue.detected(convert(self.value, self.unit, unit), unit, limit)

    def __str__(self) -> str:
        if self.censored:
            return f"<{self.detection_limit} {self.unit.value}"
        return f"{self.value} {self.unit.value}"


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueParseError(f"cannot read {text!r} as a decimal") from None
