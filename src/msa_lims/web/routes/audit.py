"""Verify the audit trail's hash chain over HTTP.

The identical check ``python -m msa_lims.db.verify_chain`` runs from a
terminal — see ``db/audit.py``'s own docstring for what "verify" means here.
Gated by ``InternalActorDep`` for now, the same posture every other read
endpoint in this API takes; a genuinely public verifier reachable with no
account at all is audit idea #8's separate scope, not this one's.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from msa_lims.db.audit import list_audit_events, verify_chain
from msa_lims.web.deps import InternalActorDep, SessionDep

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditChainVerificationOut(BaseModel):
    valid: bool
    verified_count: int
    head_hash: str | None
    broke_at_id: int | None
    broke_reason: str | None


class AuditEventOut(BaseModel):
    id: int
    table_name: str
    record_id: int
    action: str
    before: dict[str, object] | None
    after: dict[str, object] | None
    reason: str | None
    actor_id: int | None
    created_at: str | None


@router.get("/verify", response_model=AuditChainVerificationOut)
def read_audit_chain_verification(
    session: SessionDep,
    actor: InternalActorDep,
    upto: int | None = Query(default=None, ge=0),
) -> AuditChainVerificationOut:
    result = verify_chain(session, upto=upto)
    return AuditChainVerificationOut(
        valid=result.valid,
        verified_count=result.verified_count,
        head_hash=result.head_hash,
        broke_at_id=result.first_break.id if result.first_break else None,
        broke_reason=result.first_break.reason if result.first_break else None,
    )


@router.get("/events", response_model=list[AuditEventOut])
def read_audit_events(
    session: SessionDep,
    actor: InternalActorDep,
    table_name: str | None = Query(default=None),
    record_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditEventOut]:
    events = list_audit_events(session, table_name=table_name, record_id=record_id, limit=limit)
    return [
        AuditEventOut(
            id=e.id,
            table_name=e.table_name,
            record_id=e.record_id,
            action=e.action,
            before=e.before,
            after=e.after,
            reason=e.reason,
            actor_id=e.actor_id,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in events
    ]
