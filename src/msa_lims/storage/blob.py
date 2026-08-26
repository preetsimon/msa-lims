"""Content-addressed blob storage.

One function, on purpose. :func:`ensure_blob` is the only way any service
stores bytes here, which is what makes the store's promises structural
rather than conventional: content addressing (a blob's primary key is its
own sha256), write-once semantics (an existing address is returned as-is —
inserting the same evidence twice deduplicates, and no code path exists to
overwrite), and byte_count that cannot disagree with ``content`` because it
is computed from it in the same breath.
"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from msa_lims.db.models import StoredBlob


def ensure_blob(session: Session, *, content: bytes, content_type: str) -> StoredBlob:
    """Store ``content`` unless its address already exists, and return the row.

    The SELECT-before-INSERT is not a validation nicety, it *is* the store:
    identical content must deduplicate to one row, and a repeated dossier
    generation must not write a second copy of unchanged evidence. Two
    concurrent first-writes of the same content race on the primary key; one
    would flush an IntegrityError — acceptable under this repo's documented
    single-writer assumption (see ``db/audit.py``), and honest: the loser's
    bytes are already stored.
    """
    address = hashlib.sha256(content).hexdigest()
    existing = session.get(StoredBlob, address)
    if existing is not None:
        return existing
    blob = StoredBlob(
        sha256=address,
        content=content,
        content_type=content_type,
        byte_count=len(content),
    )
    session.add(blob)
    session.flush()
    return blob


def get_blob(session: Session, sha256: str) -> StoredBlob | None:
    """Fetch one stored blob by address, or ``None``."""
    return session.get(StoredBlob, sha256)


__all__ = ["ensure_blob", "get_blob"]
