"""Audit chain anchoring endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel

from msa_lims.anchoring import (
    AnchorCalendarFailedError,
    AnchorChainEmptyError,
    AnchorNotFoundError,
    OTSNotInstalledError,
    create_anchor,
    get_anchor_proof,
    get_latest_anchor,
)
from msa_lims.db.models import AuditAnchor
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import (
    FORBIDDEN_403,
    merge_responses,
)

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditAnchorOut(BaseModel):
    id: int
    anchored_hash: str
    chain_event_id: int
    ots_proof_sha256: str
    anchored_at: datetime

    @classmethod
    def from_model(cls, anchor: AuditAnchor) -> AuditAnchorOut:
        return cls(
            id=anchor.id,
            anchored_hash=anchor.anchored_hash,
            chain_event_id=anchor.chain_event_id,
            ots_proof_sha256=anchor.ots_proof_sha256,
            anchored_at=anchor.anchored_at,
        )


@router.get(
    "/anchors/latest",
    response_model=AuditAnchorOut | None,
)
def read_latest_anchor(
    session: SessionDep,
    actor: InternalActorDep,
) -> AuditAnchorOut | None:
    """The most recent audit chain anchor, or null if none exists."""
    anchor = get_latest_anchor(session)
    if anchor is None:
        return None
    return AuditAnchorOut.from_model(anchor)


@router.post(
    "/anchors",
    response_model=AuditAnchorOut,
    status_code=status.HTTP_201_CREATED,
    responses=merge_responses(FORBIDDEN_403),
)
def create_audit_anchor(
    session: SessionDep,
    actor: ActorDep,
    lab_user: LabUserDep,
) -> AuditAnchorOut:
    """Anchor the current audit chain head via OpenTimestamps.

    Creates a detached timestamp proof and submits it to OTS calendar
    servers. The proof is stored in the database (append-only) and can
    be downloaded for independent verification.
    """
    try:
        anchor = create_anchor(session, actor_id=lab_user.id)
    except OTSNotInstalledError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
    except AnchorChainEmptyError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except AnchorCalendarFailedError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    session.commit()
    return AuditAnchorOut.from_model(anchor)


@router.get(
    "/anchors/{anchor_id}/proof",
    responses=merge_responses(FORBIDDEN_403),
)
def download_anchor_proof(
    anchor_id: int,
    session: SessionDep,
    actor: InternalActorDep,
) -> dict[str, str]:
    """Download the raw OTS proof bytes for an anchor.

    The proof is sufficient to re-verify the timestamp against a Bitcoin
    node without any other context.
    """
    try:
        anchor, proof_bytes = get_anchor_proof(session, anchor_id)
    except AnchorNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return {
        "proof_hex": proof_bytes.hex(),
        "anchored_hash": anchor.anchored_hash,
        "chain_event_id": str(anchor.chain_event_id),
    }
