"""Sample identity and depth intervals.

A drill sample's identity carries geology in it. ``MSA-24-001-142.50_144.00``
says: the MSA property, the 2024 program, hole 001, the interval from 142.50 to
144.00 metres down the hole. That string is what appears on the bag, the tag,
the crucible, and eventually the certificate, and geologists read it directly.

Two things follow, and both are why this is a parsed type rather than a plain
string column:

**The interval is data, not decoration.** Two samples from the same hole must
not overlap — the same rock cannot be in two bags — and a system that keeps the
interval only inside the label cannot check that. :class:`DepthInterval` knows
how to detect an overlap; the label round-trips through it.

**Not every sample has one.** A stream sediment has coordinates and no hole. So
:class:`SampleIdentity` models drill and non-drill identity as genuinely
different shapes rather than as one shape with nullable fields nobody validates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

#: ``MSA-24-001-142.50_144.00`` — property, two-digit program year, hole number,
#: then the interval. The interval separator is an underscore rather than a
#: second hyphen so that splitting on "-" cannot mistake a depth for a hole.
_DRILL_SAMPLE = re.compile(
    r"^(?P<property_code>[A-Z]{2,5})-"
    r"(?P<program_year>\d{2})-"
    r"(?P<hole_number>\d{3,4})-"
    r"(?P<from_depth>\d+(?:\.\d+)?)_"
    r"(?P<to_depth>\d+(?:\.\d+)?)$"
)

#: ``MSA-24-SO-00417`` — property, program year, a two-letter medium code, and a
#: serial. Surface samples are numbered in a sequence, not located by a label.
_SURFACE_SAMPLE = re.compile(
    r"^(?P<property_code>[A-Z]{2,5})-"
    r"(?P<program_year>\d{2})-"
    r"(?P<medium_code>[A-Z]{2})-"
    r"(?P<serial>\d{4,6})$"
)

#: ``MSA-24-001`` — the hole itself, without an interval.
_HOLE_ID = re.compile(
    r"^(?P<property_code>[A-Z]{2,5})-(?P<program_year>\d{2})-(?P<hole_number>\d{3,4})$"
)


class SampleIdError(ValueError):
    """A sample or hole identifier could not be read."""


class DepthIntervalError(ValueError):
    """A depth interval is not physically possible."""


@dataclass(frozen=True, slots=True, order=True)
class DepthInterval:
    """A downhole interval in metres, ``from`` inclusive and ``to`` exclusive.

    Exclusive at the top end is the convention that makes contiguous sampling
    work: 142.50–144.00 followed by 144.00–145.50 covers the rock once, and a
    half-open interval says so without a tolerance fudge. Under a closed
    convention those two samples would "overlap" at exactly 144.00 and every
    contiguous run in the database would trip the check.
    """

    from_depth_m: Decimal
    to_depth_m: Decimal

    def __post_init__(self) -> None:
        if self.from_depth_m < 0:
            raise DepthIntervalError(f"depth cannot be negative: {self.from_depth_m}")
        if self.to_depth_m <= self.from_depth_m:
            raise DepthIntervalError(
                f"interval must advance down the hole: {self.from_depth_m} to {self.to_depth_m}"
            )

    @property
    def length_m(self) -> Decimal:
        """Sample length. This is the weight in a length-weighted composite."""
        return self.to_depth_m - self.from_depth_m

    def overlaps(self, other: DepthInterval) -> bool:
        """Whether two intervals claim any of the same rock.

        Half-open, so intervals that merely touch do not overlap.
        """
        return self.from_depth_m < other.to_depth_m and other.from_depth_m < self.to_depth_m

    def __str__(self) -> str:
        return f"{self.from_depth_m}_{self.to_depth_m}"


@dataclass(frozen=True, slots=True)
class SampleIdentity:
    """A parsed sample label.

    ``interval`` and ``hole_number`` are set together or not at all: a drill
    sample has both, a surface sample has neither and carries ``medium_code``
    and ``serial`` instead. :meth:`is_drill_sample` is the discriminator.
    """

    raw: str
    property_code: str
    program_year: int
    hole_number: int | None = None
    interval: DepthInterval | None = None
    medium_code: str | None = None
    serial: int | None = None

    @property
    def is_drill_sample(self) -> bool:
        return self.hole_number is not None

    @property
    def hole_id(self) -> str:
        """The hole this sample came from, for grouping samples down one hole.

        Raises for a surface sample rather than returning something empty —
        grouping surface samples by hole is a bug in the caller, and a blank
        key would silently pile all of them into one group.
        """
        if self.hole_number is None:
            raise SampleIdError(f"{self.raw} is not a drill sample; it has no hole")
        return f"{self.property_code}-{self.program_year:02d}-{self.hole_number:03d}"

    def __str__(self) -> str:
        return self.raw


def parse_sample_id(text: str) -> SampleIdentity:
    """Read a sample label.

    Tries the drill form first, then the surface form. A label matching neither
    is refused rather than stored as an opaque string: an unparseable label
    means the interval checks and the downhole grouping silently stop working
    for that sample, and finding that out at reporting time is too late.
    """
    label = text.strip().upper()
    if not label:
        raise SampleIdError("empty sample id")

    if match := _DRILL_SAMPLE.match(label):
        return SampleIdentity(
            raw=label,
            property_code=match.group("property_code"),
            program_year=int(match.group("program_year")),
            hole_number=int(match.group("hole_number")),
            interval=DepthInterval(
                _decimal(match.group("from_depth")), _decimal(match.group("to_depth"))
            ),
        )

    if match := _SURFACE_SAMPLE.match(label):
        return SampleIdentity(
            raw=label,
            property_code=match.group("property_code"),
            program_year=int(match.group("program_year")),
            medium_code=match.group("medium_code"),
            serial=int(match.group("serial")),
        )

    raise SampleIdError(
        f"cannot read {text!r} as a sample id; expected a drill label like "
        "'MSA-24-001-142.50_144.00' or a surface label like 'MSA-24-SO-00417'"
    )


def parse_hole_id(text: str) -> tuple[str, int, int]:
    """Read a drill hole identifier, returning (property code, year, number)."""
    label = text.strip().upper()
    if match := _HOLE_ID.match(label):
        return (
            match.group("property_code"),
            int(match.group("program_year")),
            int(match.group("hole_number")),
        )
    raise SampleIdError(f"cannot read {text!r} as a hole id; expected a label like 'MSA-24-001'")


def find_overlaps(
    intervals: list[tuple[str, DepthInterval]],
) -> list[tuple[str, str]]:
    """Return every pair of labels whose intervals claim the same rock.

    Reports *all* conflicting pairs rather than raising on the first. A geologist
    correcting a submission wants the whole list in one pass, not one error per
    re-upload.
    """
    ordered = sorted(intervals, key=lambda item: item[1])
    conflicts: list[tuple[str, str]] = []
    for index, (label, interval) in enumerate(ordered):
        for other_label, other in ordered[index + 1 :]:
            # Sorted by start depth, so once a later interval starts at or after
            # this one ends, no interval after it can overlap either.
            if other.from_depth_m >= interval.to_depth_m:
                break
            if interval.overlaps(other):
                conflicts.append((label, other_label))
    return conflicts


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        raise SampleIdError(f"cannot read {text!r} as a depth") from None
