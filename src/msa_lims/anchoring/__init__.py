"""Audit chain anchoring via OpenTimestamps.

Anchoring ties the audit chain head to a public blockchain (Bitcoin),
proving *when* the chain existed independently of any clock the lab
controls.  The anchor sits beside the chain — it does not modify it.
Verification is still :func:`msa_lims.db.audit.verify_chain`; the anchor
additionally proves the chain head's existence at a point in time.

The OpenTimestamps library is an optional dependency (``pip install
msa-lims[ots]``).  If it is not installed, the anchoring functions raise
:class:`OTSNotInstalledError` and the rest of the system is unaffected.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.audit import verify_chain
from msa_lims.db.models import AuditAnchor

_log = logging.getLogger(__name__)


class OTSNotInstalledError(RuntimeError):
    """The ``opentimestamps`` package is not installed."""


class AnchorChainEmptyError(RuntimeError):
    """Cannot anchor an empty audit chain."""


class AnchorNotFoundError(RuntimeError):
    """No anchor with this id exists."""


class AnchorProofCorruptedError(RuntimeError):
    """The stored OTS proof no longer hashes to what the row claims."""


class AnchorCalendarFailedError(RuntimeError):
    """No calendar server could be reached — the OTS proof cannot be created."""


def _ots_available() -> bool:
    """Check if the opentimestamps package is installed."""
    try:
        import importlib.util

        return importlib.util.find_spec("opentimestamps") is not None
    except (ImportError, ValueError):
        return False


def get_latest_anchor(session: Session) -> AuditAnchor | None:
    """The most recent anchor, or ``None`` if none exists."""
    return session.scalar(
        select(AuditAnchor).order_by(AuditAnchor.id.desc()).limit(1)
    )


def create_anchor(session: Session, *, actor_id: int | None = None) -> AuditAnchor:
    """Snapshot the current chain head and anchor it via OpenTimestamps.

    1. Verifies the chain and gets the head hash.
    2. Creates an OpenTimestamps detached timestamp from the hash.
    3. Submits to the default calendar servers (best-effort).
    4. Stores the proof in ``audit_anchor`` (append-only).

    Raises :class:`OTSNotInstalledError` if the library is missing,
    :class:`AnchorChainEmptyError` if the chain has no rows.
    """
    if not _ots_available():
        raise OTSNotInstalledError(
            "opentimestamps package is not installed; "
            "install with: pip install msa-lims[ots]"
        )

    # Step 1: verify chain and get head
    verification = verify_chain(session)
    if verification.head_hash is None:
        raise AnchorChainEmptyError("audit chain is empty — nothing to anchor")

    head_hash = verification.head_hash

    # Find the chain head event id
    from msa_lims.db.models import AuditEvent

    head_event = session.scalar(
        select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)
    )
    if head_event is None:
        raise AnchorChainEmptyError("audit chain is empty")

    # Step 2: create OTS detached timestamp
    import opentimestamps.core.op as ops  # type: ignore[import-untyped]
    import opentimestamps.core.timestamp as ts  # type: ignore[import-untyped]

    hash_bytes = bytes.fromhex(head_hash)
    stamp = ts.Timestamp(hash_bytes)
    detached = ts.DetachedTimestampFile(ops.OpSHA256(), stamp)

    # Step 3: submit to calendar servers (best-effort, merge attestations)
    n_success = _submit_to_calendars(detached, hash_bytes)
    if n_success == 0:
        raise AnchorCalendarFailedError(
            "no OpenTimestamps calendar server responded; "
            "cannot create an anchored proof"
        )

    # Step 4: serialize the proof
    proof_bytes = detached.serialize_to_bytes()
    proof_sha256 = hashlib.sha256(proof_bytes).hexdigest()

    # Step 5: store in database
    anchor = AuditAnchor(
        anchored_hash=head_hash,
        chain_event_id=head_event.id,
        ots_proof=proof_bytes,
        ots_proof_sha256=proof_sha256,
        anchored_at=datetime.now(UTC),
    )
    session.add(anchor)
    session.flush()

    # Audit the anchor creation
    from msa_lims.db.audit import record_audit_event

    record_audit_event(
        session,
        table_name="audit_anchor",
        record_id=anchor.id,
        action="INSERT",
        actor_id=actor_id,
        after={
            "anchored_hash": head_hash,
            "chain_event_id": head_event.id,
        },
    )

    return anchor


def _submit_to_calendars(
    detached: object,  # DetachedTimestampFile but typed loosely to avoid OTS import at module level
    hash_bytes: bytes,
    *,
    timeout: int = 5,
) -> int:
    """Submit the hash to public calendar servers and merge attestations.

    Each calendar call is best-effort; failures are logged but not fatal.
    Returns the number of calendars that responded successfully.
    """
    from opentimestamps.calendar import RemoteCalendar  # type: ignore[import-untyped]

    calendar_urls = [
        "https://a.pool.opentimestamps.org",
        "https://b.pool.opentimestamps.org",
        "https://a.pool.eternitywall.com",
    ]
    n_success = 0
    for url in calendar_urls:
        try:
            cal = RemoteCalendar(url)
            attestation = cal.submit(hash_bytes, timeout=timeout)
            detached.timestamp.merge(attestation)  # type: ignore[attr-defined]
            n_success += 1
        except Exception:
            _log.debug("OTS calendar %s failed; continuing", url, exc_info=True)
            continue
    return n_success


def get_anchor_proof(session: Session, anchor_id: int) -> tuple[AuditAnchor, bytes]:
    """Return the anchor and its OTS proof, hash-verified on the way out."""
    anchor = session.get(AuditAnchor, anchor_id)
    if anchor is None:
        raise AnchorNotFoundError(f"no anchor with id {anchor_id}")

    actual = hashlib.sha256(anchor.ots_proof).hexdigest()
    if actual != anchor.ots_proof_sha256:
        raise AnchorProofCorruptedError(
            f"anchor {anchor_id} proof has drifted from its recorded hash"
        )

    return anchor, anchor.ots_proof
