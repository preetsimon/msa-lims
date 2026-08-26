"""Hash-chaining for the audit trail — audit idea #1, "The Ledger That
Signs Itself" (AUDIT_AND_BREAKTHROUGHS.md).

``audit_event`` is already append-only by Postgres grant: the application
role holds no UPDATE or DELETE on it. That is enforceable against this
application and invisible to everyone else — a DBA with a backup restore,
or a support engineer with a one-off `UPDATE`, leaves no trace a grant can
catch. Hash-chaining makes tampering *detectable* instead of merely
forbidden: every row's ``entry_hash`` commits to its own content and the
previous row's hash, the same construction a blockchain uses for block
headers. Flip one bit anywhere in the history and every hash after it stops
matching what a fresh walk of the chain recomputes — a chain that still
verifies is a chain nothing touched, checkable by this database or a
completely independent script fed nothing but the rows.

Pure module — no session, no clock — matching every other ``domain/``
module's own discipline: the hash a caller gets back is reproducible from
the same inputs years later, the identical guarantee
:mod:`msa_lims.domain.assay` already gives a grade calculation.

External anchoring (OpenTimestamps, proving the chain's head existed by a
given Bitcoin block) is real remaining scope from the audit idea's own
sketch, deliberately not built in this pass — see PROGRESS.md. This module
only closes the chain-integrity half: detecting *that* something changed,
not proving *when* the untampered version existed relative to the outside
world.
"""

from __future__ import annotations

import hashlib

from msa_lims.domain.canonical import canonical_json

#: The genesis row — the first audit_event ever written — has no
#: predecessor. A fixed, documented sentinel (not `None`, not an empty
#: string) stands in for "nothing came before this" when computing its hash,
#: so the genesis row's own hash is exactly as reproducible as every other
#: row's, and "the chain has no start yet" can never be confused with a
#: previous hash that happened to be empty.
GENESIS_PREV_HASH = "0" * 64


def canonical_entry(
    *,
    prev_entry_hash: str,
    table_name: str,
    record_id: int,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    reason: str | None,
    actor_id: int | None,
    actor_ip: str | None,
) -> str:
    """The exact bytes one entry's hash commits to.

    Serialized through :func:`msa_lims.domain.canonical.canonical_json` —
    the same function the provenance dossier's seal uses, so the two
    features cannot drift into disagreeing about what "canonical" means.
    The byte-determinism discipline is `certificates/pdf.py`'s, applied
    here to a signed *fact* rather than a signed document.

    Exported (not folded into :func:`compute_entry_hash`) so an independent
    verifier can recompute this exact payload from the stored columns alone
    and hash it itself, rather than trusting this module's own arithmetic —
    "recompute, don't trust" is the same posture
    `certificates/service.py`'s own hash check already takes on a PDF.
    """
    return canonical_json(
        {
            "prev_entry_hash": prev_entry_hash,
            "table_name": table_name,
            "record_id": record_id,
            "action": action,
            "before": before,
            "after": after,
            "reason": reason,
            "actor_id": actor_id,
            "actor_ip": actor_ip,
        }
    )


def compute_entry_hash(
    *,
    prev_entry_hash: str | None,
    table_name: str,
    record_id: int,
    action: str,
    before: dict[str, object] | None,
    after: dict[str, object] | None,
    reason: str | None,
    actor_id: int | None,
    actor_ip: str | None,
) -> str:
    """``sha256(prev_entry_hash ∥ canonical(entry))``, hex-encoded.

    ``prev_entry_hash=None`` means "no predecessor" (the genesis row) and is
    substituted with :data:`GENESIS_PREV_HASH` before hashing — the stored
    column stays genuinely `NULL` for that row (mirroring `supersedes_id`'s
    own "nothing before this" convention elsewhere in the schema); only the
    hash computation itself needs a concrete value to hash.
    """
    prev = prev_entry_hash if prev_entry_hash is not None else GENESIS_PREV_HASH
    entry = canonical_entry(
        prev_entry_hash=prev,
        table_name=table_name,
        record_id=record_id,
        action=action,
        before=before,
        after=after,
        reason=reason,
        actor_id=actor_id,
        actor_ip=actor_ip,
    )
    return hashlib.sha256((prev + entry).encode("utf-8")).hexdigest()
