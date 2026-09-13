"""Sentinel export format tests.

Golden data is copied from fireAssay's own parser tests
(``tests/unit/test_generic_csv_parser.py`` and
``tests/unit/test_wide_icp_parser.py``) — the same bytes those parsers
were written against.  If the LIMS output diverges from these expectations,
Sentinel's ingestion will reject the file.
"""

from __future__ import annotations

from msa_lims.sentinel.export import QcRow, to_generic_csv_v1, to_wide_icp_v1

# ── Generic CSV golden data ────────────────────────────────────────────
# Copied from fireAssay tests/unit/test_generic_csv_parser.py:7-11

GENERIC_CSV_GOLDEN = (
    "SampleID,Analyte,Value,Unit,Timestamp,BatchID\r\n"
    "LAN26-09421-001,Au,1.42,g/t,2026-07-24T08:15:30,LAN26-09421-F01\r\n"
    "STD-OXP112,Au,2.15,g/t,2026-07-24T08:16:10,LAN26-09421-F01\r\n"
    "BLK-01,Au,<0.01,g/t,2026-07-24T08:16:50,LAN26-09421-F01\r\n"
)

GENERIC_CSV_ROWS = [
    QcRow(
        sample_id="LAN26-09421-001",
        analyte="Au",
        value="1.42",
        unit="g/t",
        timestamp="2026-07-24T08:15:30",
        batch_id="LAN26-09421-F01",
    ),
    QcRow(
        sample_id="STD-OXP112",
        analyte="Au",
        value="2.15",
        unit="g/t",
        timestamp="2026-07-24T08:16:10",
        batch_id="LAN26-09421-F01",
    ),
    QcRow(
        sample_id="BLK-01",
        analyte="Au",
        value="<0.01",
        unit="g/t",
        timestamp="2026-07-24T08:16:50",
        batch_id="LAN26-09421-F01",
    ),
]


# ── Wide ICP golden data ──────────────────────────────────────────────
# Copied from fireAssay tests/unit/test_wide_icp_parser.py:25-29
# The parser expects tab-separated, \r\n line endings.

WIDE_ICP_GOLDEN = (
    "SampleID\tBatchID\tTimestamp\tOperator\tAu (g/t)\tAg (ppm)\tCu (%)\r\n"
    "LAN26-09421-001\tLAN26-09421-F01\t2026-07-24T08:15:30\tj.smith\t1.42\t0.03\t\r\n"
    "STD-OXP112\tLAN26-09421-F01\t2026-07-24T08:16:10\tj.smith\t2.15\t<0.01\t0.004\r\n"
)

WIDE_ICP_ROWS = [
    QcRow(
        sample_id="LAN26-09421-001",
        analyte="Au",
        value="1.42",
        unit="g/t",
        timestamp="2026-07-24T08:15:30",
        batch_id="LAN26-09421-F01",
        operator="j.smith",
    ),
    QcRow(
        sample_id="LAN26-09421-001",
        analyte="Ag",
        value="0.03",
        unit="ppm",
        timestamp="2026-07-24T08:15:30",
        batch_id="LAN26-09421-F01",
        operator="j.smith",
    ),
    QcRow(
        sample_id="LAN26-09421-001",
        analyte="Cu",
        value="",
        unit="%",
        timestamp="2026-07-24T08:15:30",
        batch_id="LAN26-09421-F01",
        operator="j.smith",
    ),
    QcRow(
        sample_id="STD-OXP112",
        analyte="Au",
        value="2.15",
        unit="g/t",
        timestamp="2026-07-24T08:16:10",
        batch_id="LAN26-09421-F01",
        operator="j.smith",
    ),
    QcRow(
        sample_id="STD-OXP112",
        analyte="Ag",
        value="<0.01",
        unit="ppm",
        timestamp="2026-07-24T08:16:10",
        batch_id="LAN26-09421-F01",
        operator="j.smith",
    ),
    QcRow(
        sample_id="STD-OXP112",
        analyte="Cu",
        value="0.004",
        unit="%",
        timestamp="2026-07-24T08:16:10",
        batch_id="LAN26-09421-F01",
        operator="j.smith",
    ),
]


class TestGenericCsv:
    def test_matches_the_golden_output(self) -> None:
        assert to_generic_csv_v1(GENERIC_CSV_ROWS) == GENERIC_CSV_GOLDEN

    def test_empty_input_produces_header_only(self) -> None:
        result = to_generic_csv_v1([])
        assert result.startswith("SampleID,Analyte,Value,Unit,Timestamp,BatchID\r\n")

    def test_censored_value_passes_through_untouched(self) -> None:
        rows = [
            QcRow(
                sample_id="BLK-01",
                analyte="Au",
                value="<0.01",
                unit="g/t",
                timestamp="2026-07-24T08:16:50",
                batch_id="LAN26-09421-F01",
            ),
        ]
        result = to_generic_csv_v1(rows)
        assert "<0.01" in result

    def test_formula_injection_is_defused(self) -> None:
        rows = [
            QcRow(
                sample_id="=cmd|'/c calc'!A1",
                analyte="Au",
                value="1.00",
                unit="g/t",
                timestamp="2026-07-24T08:00:00",
                batch_id="TEST",
            ),
        ]
        result = to_generic_csv_v1(rows)
        assert "'=cmd|'/c calc'!A1" in result

    def test_negative_numbers_are_not_prefixed(self) -> None:
        rows = [
            QcRow(
                sample_id="S1",
                analyte="Au",
                value="-1.5",
                unit="g/t",
                timestamp="2026-07-24T08:00:00",
                batch_id="TEST",
            ),
        ]
        result = to_generic_csv_v1(rows)
        assert "-1.5" in result
        assert "'-1.5" not in result


class TestWideIcp:
    def test_matches_the_golden_output(self) -> None:
        assert to_wide_icp_v1(WIDE_ICP_ROWS) == WIDE_ICP_GOLDEN

    def test_empty_input_returns_empty_string(self) -> None:
        assert to_wide_icp_v1([]) == ""

    def test_empty_cell_for_missing_analyte(self) -> None:
        """LAN26-09421-001 has no Cu measurement — the cell must be empty."""
        result = to_wide_icp_v1(WIDE_ICP_ROWS)
        lines = result.split("\r\n")
        lan_line = next(line for line in lines if line.startswith("LAN26-09421-001"))
        assert lan_line.endswith("\t")  # empty Cu (%) cell

    def test_censored_value_in_wide_format(self) -> None:
        result = to_wide_icp_v1(WIDE_ICP_ROWS)
        assert "<0.01" in result

    def test_formula_injection_is_defused_in_wide_format(self) -> None:
        rows = [
            QcRow(
                sample_id="=EVIL()",
                analyte="Au",
                value="1.00",
                unit="g/t",
                timestamp="2026-07-24T08:00:00",
                batch_id="TEST",
                operator="test",
            ),
        ]
        result = to_wide_icp_v1(rows)
        assert "'=EVIL()" in result

    def test_analytes_appear_in_encounter_order(self) -> None:
        rows = [
            QcRow(
                sample_id="S1", analyte="Zn", value="1",
                unit="ppm", timestamp="t", batch_id="B", operator="o",
            ),
            QcRow(
                sample_id="S1", analyte="Cu", value="2",
                unit="ppm", timestamp="t", batch_id="B", operator="o",
            ),
            QcRow(
                sample_id="S1", analyte="Au", value="3",
                unit="ppm", timestamp="t", batch_id="B", operator="o",
            ),
        ]
        result = to_wide_icp_v1(rows)
        header = result.split("\r\n")[0]
        assert header == "SampleID\tBatchID\tTimestamp\tOperator\tZn (ppm)\tCu (ppm)\tAu (ppm)"
