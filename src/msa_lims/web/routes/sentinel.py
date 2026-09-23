"""QC Sentinel integration — submit batches and poll per-run evaluations.

POST /api/batches/{id}/submit-to-sentinel
GET  /api/batches/{id}/sentinel-verdict

The verdict is advisory: it lands in ``sentinel_submission.verdict`` and is
never written back to any result or sample row.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from msa_lims.config import get_settings
from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import Batch, Instrument, SentinelSubmission
from msa_lims.domain.enums import MAY_SIGN_CERTIFICATE
from msa_lims.qc_dossiers.service import build_qc_dossier, dossier_payload
from msa_lims.sentinel.client import SentinelClient
from msa_lims.sentinel.export import QcRow, to_generic_csv_v1
from msa_lims.web.deps import ActorDep, LabUserDep, SessionDep
from msa_lims.web.routes.error_responses import (
    BATCH_NOT_FOUND,
    CONFLICT_409,
    FORBIDDEN_403,
    merge_responses,
)

router = APIRouter(prefix="/api", tags=["sentinel"])


def _batch_or_404(session: SessionDep, batch_id: int) -> Batch:
    batch = session.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"batch {batch_id!r} not found",
        )
    return batch


def _resolve_batch_instrument(session: SessionDep, batch: Batch) -> tuple[int, int]:
    """Resolve the instrument and method for a batch.

    Returns (instrument_id, method_id).  Raises 409 if instrument is missing.
    """
    if batch.instrument_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"batch {batch.batch_number!r} has no instrument assigned; "
                "assign an instrument before submitting to Sentinel"
            ),
        )

    instrument = session.get(Instrument, batch.instrument_id)
    if instrument is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"instrument {batch.instrument_id!r} not found",
        )

    # For method_id, we use a default of 1 (generic fire assay) since the
    # LIMS doesn't yet track analytical methods as a first-class entity.
    # TODO: wire real method registry when Sentinel method IDs are mapped.
    method_id = 1

    return batch.instrument_id, method_id


def _qc_rows_from_dossier(payload_dict: dict[str, object]) -> list[QcRow]:
    """Convert a dossier payload dict into QcRow instances for export."""
    rows: list[QcRow] = []
    batch_number = str(payload_dict.get("batch_number", ""))
    raw_entries = payload_dict.get("entries", [])
    entries = raw_entries if isinstance(raw_entries, list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        au = entry.get("au")
        if au is None or not isinstance(au, dict):
            continue
        value = str(au.get("value", ""))
        unit = str(au.get("unit", ""))
        censored = au.get("censored", False)
        if censored and entry.get("certified_au_value_g_t") is not None:
            value = f"<{entry['certified_au_value_g_t']}"
        rows.append(
            QcRow(
                sample_id=str(entry.get("name", "")),
                analyte="Au",
                value=value,
                unit=unit,
                timestamp="",
                batch_id=batch_number,
            )
        )
    return rows


@router.post(
    "/batches/{batch_id}/submit-to-sentinel",
    status_code=status.HTTP_202_ACCEPTED,
    responses=merge_responses(FORBIDDEN_403, BATCH_NOT_FOUND, CONFLICT_409),
)
def submit_to_sentinel(
    batch_id: int,
    session: SessionDep,
    actor: ActorDep,
    analyst: LabUserDep,
) -> dict[str, object]:
    """Submit a batch's QC dossier to Sentinel.

    The batch must have a sealed dossier (``batch.qc_dossier_sha256 IS NOT
    NULL``) and at least one crucible with an assigned instrument.  If
    Sentinel is disabled or unreachable, the attempt is still recorded.
    """
    if actor.role not in MAY_SIGN_CERTIFICATE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role {actor.role.value!r} may not submit to Sentinel",
        )

    batch = _batch_or_404(session, batch_id)
    if batch.qc_dossier_sha256 is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"batch {batch_id!r} has no sealed QC dossier",
        )

    settings = get_settings()
    if not settings.sentinel_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sentinel integration is disabled (MSA_SENTINEL_ENABLED=false)",
        )

    # Resolve instrument and method from the batch.
    instrument_id, method_id = _resolve_batch_instrument(session, batch)

    # Build the export from the sealed dossier.
    dossier = build_qc_dossier(
        session,
        batch_id=batch_id,
        blank_threshold_g_t=Decimal(settings.blank_max_grade_g_t),
        max_duplicate_rpd_percent=Decimal(settings.max_duplicate_rpd_percent),
    )
    payload_dict = dossier_payload(dossier)
    rows = _qc_rows_from_dossier(payload_dict)
    csv_bytes = to_generic_csv_v1(rows).encode("utf-8")
    payload_sha256 = hashlib.sha256(csv_bytes).hexdigest()

    # Send to Sentinel as multipart.
    client = SentinelClient()
    result = client.submit_batch(
        csv_bytes,
        fmt="generic_csv_v1",
        batch_id=batch_id,
        instrument_id=instrument_id,
        method_id=method_id,
        filename=f"batch_{batch_id}_qc.csv",
    )

    # Record the attempt (append-only).
    submission = SentinelSubmission(
        batch_id=batch_id,
        format="generic_csv_v1",
        payload_sha256=payload_sha256,
        sentinel_import_id=result.sentinel_import_id,
        submitted_by=analyst.id,
        http_status=result.http_status,
    )
    session.add(submission)
    session.flush()

    record_audit_event(
        session,
        table_name="sentinel_submission",
        record_id=submission.id,
        action="INSERT",
        actor_id=analyst.id,
        before=None,
        after={
            "batch_id": batch_id,
            "format": "generic_csv_v1",
            "payload_sha256": payload_sha256,
            "http_status": result.http_status,
            "instrument_id": instrument_id,
            "method_id": method_id,
            "error": result.error,
        },
    )
    session.commit()

    return {
        "submission_id": submission.id,
        "http_status": result.http_status,
        "sentinel_import_id": result.sentinel_import_id,
        "run_ids": result.run_ids,
        "error": result.error,
    }


@router.get(
    "/batches/{batch_id}/sentinel-verdict",
    responses=merge_responses(BATCH_NOT_FOUND),
)
def get_sentinel_verdict(
    batch_id: int,
    session: SessionDep,
    actor: ActorDep,
) -> dict[str, object]:
    """Get the latest Sentinel verdict for a batch.

    Queries Sentinel for per-run evaluations of the batch's submitted runs.
    Returns the verdict from the most recent submission, or an empty verdict
    if none has been returned yet.
    """
    _batch_or_404(session, batch_id)

    # Find the most recent submission.
    stmt = (
        select(SentinelSubmission)
        .where(SentinelSubmission.batch_id == batch_id)
        .order_by(SentinelSubmission.submitted_at.desc())
        .limit(1)
    )
    latest = session.execute(stmt).scalar_one_or_none()

    if latest is None:
        return {
            "status": "never_submitted",
            "verdict": None,
            "submitted_at": None,
            "last_polled_at": None,
        }

    if latest.verdict is not None:
        return {
            "status": "verdicted",
            "verdict": latest.verdict,
            "submitted_at": latest.submitted_at.isoformat() if latest.submitted_at else None,
            "last_polled_at": (
                latest.last_polled_at.isoformat() if latest.last_polled_at else None
            ),
            "http_status": latest.http_status,
        }

    return {
        "status": "pending",
        "verdict": None,
        "submitted_at": latest.submitted_at.isoformat() if latest.submitted_at else None,
        "last_polled_at": (
            latest.last_polled_at.isoformat() if latest.last_polled_at else None
        ),
        "http_status": latest.http_status,
    }
