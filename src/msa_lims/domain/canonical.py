"""One canonical JSON serialization, used everywhere a hash commits to data.

Two features need to turn a structure into bytes that hash the same way
every time, on every machine, years apart: the audit hash chain
(:mod:`msa_lims.domain.audit_chain`, idea #1) and the provenance dossier's
seal (:mod:`msa_lims.provenance.service`, idea #3). Two independently
written serializers that happened to agree today would be one refactor away
from silently disagreeing — the identical reasoning
:func:`msa_lims.domain.sample_id.format_hole_id` was extracted under in
Phase 1, applied here to bytes instead of a hole label.

Sorted keys and fixed separators (JCS-style), ASCII-escaped so the output
is byte-identical regardless of the reader's encoding assumptions. Pure: no
session, no clock, no I/O.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """The one true rendering of ``payload`` for hashing.

    Not for display and not for API responses — those go through Pydantic,
    which is free to order keys however it likes. This exists so that two
    parties holding the same facts compute the same bytes.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_sha256(payload: Any) -> str:
    """``sha256(canonical_json(payload))``, hex-encoded."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
