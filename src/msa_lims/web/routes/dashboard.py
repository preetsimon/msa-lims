"""Dashboard statistics — lab-wide overview for the home page."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from msa_lims.db.models import Batch, Certificate, Client, Sample, Submission
from msa_lims.domain.enums import SampleStatus
from msa_lims.web.deps import InternalActorDep, SessionDep
from msa_lims.web.schemas import DashboardStatsOut

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStatsOut)
def read_stats(
    session: SessionDep,
    actor: InternalActorDep,
) -> DashboardStatsOut:
    """Lab-wide counts for the dashboard home page."""
    total_samples = session.scalar(select(func.count(Sample.id))) or 0
    total_clients = session.scalar(select(func.count(Client.id))) or 0
    total_submissions = session.scalar(select(func.count(Submission.id))) or 0
    total_batches = session.scalar(select(func.count(Batch.id))) or 0
    total_certificates = session.scalar(select(func.count(Certificate.id))) or 0

    samples_by_status = {}
    for status_val in SampleStatus:
        count = session.scalar(
            select(func.count(Sample.id)).where(Sample.status == status_val)
        ) or 0
        samples_by_status[status_val.value] = count

    return DashboardStatsOut(
        total_samples=total_samples,
        total_clients=total_clients,
        total_submissions=total_submissions,
        total_batches=total_batches,
        total_certificates=total_certificates,
        samples_by_status=samples_by_status,
    )
