"""QC Sentinel integration — export only, advisory disposition.

This package exports batch QC data in the formats Sentinel's ingestion
parsers accept.  It does **not** import, parse, or store anything from
Sentinel: the boundary between the two systems is the CSV file, and
this module lives entirely on the LIMS side of it.

The two export functions are pure: no session, no clock, no HTTP.
"""

from __future__ import annotations
