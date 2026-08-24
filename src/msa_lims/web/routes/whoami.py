"""One endpoint whose entire job is to prove authentication actually runs.

Every write endpoint from Phase 1 onward depends on :data:`ActorDep`. This
route has no other purpose than to be the cheapest possible thing that
exercises it — a client, a curl script, or a Phase-1 developer can hit it and
find out who the system thinks they are before trusting any write path built
on the same dependency.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from msa_lims.web.deps import ActorDep

router = APIRouter(prefix="/api", tags=["auth"])


class WhoAmIResponse(BaseModel):
    name: str
    role: str


@router.get("/me", response_model=WhoAmIResponse)
async def whoami(actor: ActorDep) -> WhoAmIResponse:
    return WhoAmIResponse(name=actor.name, role=actor.role.value)
