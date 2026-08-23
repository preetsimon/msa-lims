"""Liveness and readiness.

The database and the QC Sentinel integration are reported **separately**, and
their failures do not weigh the same. Without a database the LIMS cannot accept
a sample, so that is unhealthy. Without Sentinel the lab keeps working exactly
as it did before the integration existed — QC rows queue up and push later — so
that is ``degraded`` at HTTP 200. Collapsing the two into one boolean would page
somebody at 3am because a surveillance system they do not need to assay samples
went down.
"""

from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from msa_lims.config import get_settings
from msa_lims.db.session import get_engine

router = APIRouter(tags=["health"])


class ComponentHealth(BaseModel):
    status: Literal["ok", "unavailable", "not_configured"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    version: str
    database: ComponentHealth
    qc_sentinel: ComponentHealth


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    database = _check_database()
    sentinel = await _check_sentinel()

    if database.status != "ok":
        overall: Literal["healthy", "degraded", "unhealthy"] = "unhealthy"
    elif sentinel.status == "unavailable":
        overall = "degraded"
    else:
        overall = "healthy"

    return HealthResponse(
        status=overall,
        version="0.1.0",
        database=database,
        qc_sentinel=sentinel,
    )


def _check_database() -> ComponentHealth:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # The exception class, not the message: a connection error can carry the
        # password in its string representation.
        return ComponentHealth(status="unavailable", detail=exc.__class__.__name__)
    return ComponentHealth(status="ok")


async def _check_sentinel() -> ComponentHealth:
    settings = get_settings()
    if not settings.sentinel_enabled:
        return ComponentHealth(status="not_configured", detail="integration disabled")
    try:
        async with httpx.AsyncClient(timeout=settings.sentinel_timeout_seconds) as client:
            response = await client.get(f"{settings.sentinel_base_url}/health")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return ComponentHealth(status="unavailable", detail=exc.__class__.__name__)
    return ComponentHealth(status="ok")
