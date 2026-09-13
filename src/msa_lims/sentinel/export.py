"""QC Sentinel export — pure functions that produce CSV Sentinel can ingest.

Two formats, matching the two parsers Sentinel already ships:

* **Generic CSV** — one result per row.  Columns: SampleID, Analyte, Value,
  Unit, Timestamp, BatchID.  This is the canonical format; every other
  representation is a specialised view of it.

* **Wide ICP** — one sample per row, analytes as columns.  Columns:
  SampleID, BatchID, Timestamp, Operator, then ``Analyte (unit)`` columns
  for each element present in the batch.  Empty cells mean the element was
  not measured for that sample.

Both functions are pure: no session, no clock, no HTTP.  The caller
assembles ``QcRow`` instances from ORM data (typically via
``qc_dossiers/service.py``), and these functions turn them into bytes.

**Numbers are written as-is.**  No rounding, no thousands separators, no
scientific notation — the same discipline fireAssay's own CSV export
follows.  Censored values travel as ``<0.01`` strings, never as floats.

**Formula injection is defused.**  Cells whose text begins with ``=``,
``+``, ``-``, ``@``, or a leading tab/return are prefixed with an
apostrophe, matching fireAssay's ``sanitise()`` behaviour exactly.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "QcRow",
    "to_generic_csv_v1",
    "to_wide_icp_v1",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True, slots=True)
class QcRow:
    """One QC result, expressed as plain strings so format functions stay pure.

    The caller is responsible for converting ``Decimal`` / ``MeasuredValue``
    to strings before constructing this object — the format functions must
    not touch the domain model, and must not round or reformat numbers.
    """

    sample_id: str
    analyte: str
    value: str
    unit: str
    timestamp: str
    batch_id: str
    operator: str = ""


# ── formula injection defence ──────────────────────────────────────────


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _sanitise(value: Any) -> str:
    """Render a value for a spreadsheet cell, defusing formula injection.

    A negative number is left alone — ``-1.5`` is a quantity, not a formula.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"

    text = str(value)
    if not text:
        return ""
    if text.startswith(_FORMULA_PREFIXES) and not _is_number(text):
        return "'" + text
    return text


# ── generic CSV (one result per row) ──────────────────────────────────


def to_generic_csv_v1(rows: Sequence[QcRow]) -> str:
    """Produce a generic CSV that Sentinel's ``GenericCsvParser`` ingests.

    Columns: SampleID, Analyte, Value, Unit, Timestamp, BatchID.

    Returns the full file as a string.  For very large batches the caller
    can stream instead — this function exists for clarity and testability.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["SampleID", "Analyte", "Value", "Unit", "Timestamp", "BatchID"])
    for row in rows:
        writer.writerow([
            _sanitise(row.sample_id),
            _sanitise(row.analyte),
            _sanitise(row.value),
            _sanitise(row.unit),
            _sanitise(row.timestamp),
            _sanitise(row.batch_id),
        ])
    return buf.getvalue()


# ── wide ICP (one sample per row, analytes as columns) ─────────────────


def to_wide_icp_v1(rows: Sequence[QcRow]) -> str:
    """Produce a wide-format TSV that Sentinel's ``WideIcpParser`` ingests.

    Columns: SampleID, BatchID, Timestamp, Operator, then one column per
    analyte named ``Analyte (unit)``.  Empty cells mean the element was not
    measured for that sample.

    Returns the full file as a string (tab-separated, ``\\r\\n`` line
    endings — matching the wide ICP parser's expectations).
    """
    if not rows:
        return ""

    # Discover analytes in encounter order.
    analyte_order: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.analyte, row.unit)
        if key not in seen:
            seen.add(key)
            analyte_order.append(key)

    # Build a lookup: (sample_id, batch_id, analyte, unit) -> value.
    lookup: dict[tuple[str, str, str, str], str] = {}
    sample_info: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        lookup[(row.sample_id, row.batch_id, row.analyte, row.unit)] = row.value
        sample_info[(row.sample_id, row.batch_id)] = (row.timestamp, row.operator)

    # Emit rows in encounter order of (sample_id, batch_id).
    sample_order: list[tuple[str, str]] = []
    seen_samples: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.sample_id, row.batch_id)
        if key not in seen_samples:
            seen_samples.add(key)
            sample_order.append(key)

    buf = io.StringIO()
    # Header: SampleID \t BatchID \t Timestamp \t Operator \t Analyte (unit) ...
    headers = ["SampleID", "BatchID", "Timestamp", "Operator"]
    for analyte, unit in analyte_order:
        headers.append(f"{analyte} ({unit})")
    buf.write("\t".join(headers))
    buf.write("\r\n")

    for sample_id, batch_id in sample_order:
        timestamp, operator = sample_info[(sample_id, batch_id)]
        cells = [
            _sanitise(sample_id),
            _sanitise(batch_id),
            _sanitise(timestamp),
            _sanitise(operator),
        ]
        for analyte, unit in analyte_order:
            cells.append(_sanitise(lookup.get((sample_id, batch_id, analyte, unit), "")))
        buf.write("\t".join(cells))
        buf.write("\r\n")

    return buf.getvalue()
