"""Drill hole registration.

The last piece of reference data submission intake depends on. A drill sample
resolves to its hole by parsing the label and looking up
``(project_id, hole_id)`` — see
:meth:`msa_lims.submissions.service.SubmissionService._resolve_drill_holes` —
and that lookup only works if this module stores the hole label in exactly the
same canonical form the parser computes. Both routes go through
:func:`msa_lims.domain.sample_id.canonical_hole_id` /
:func:`~msa_lims.domain.sample_id.format_hole_id` for that reason: two
functions that happened to agree by construction would be one refactor away
from silently disagreeing.

Registered by ``BENCH_ROLES``, not ``MAY_MANAGE_ACCOUNTS``. A hole's collar
coordinates and depth typically arrive with the drill log accompanying a core
shipment — the same moment a submission is received — rather than as a
business decision about who the lab works for.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.clients.service import ProjectNotFoundError
from msa_lims.db.models import AuditEvent, DrillHole, LabUser, Project
from msa_lims.domain.enums import Role
from msa_lims.domain.lifecycle import BENCH_ROLES, InsufficientRoleError
from msa_lims.domain.sample_id import SampleIdError, canonical_hole_id


class DrillHoleValidationError(ValueError):
    """One or more problems with a drill hole registration, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class DrillHoleInput:
    project_id: int
    hole_id: str
    easting: Decimal | None = None
    northing: Decimal | None = None
    elevation_m: Decimal | None = None
    utm_zone: str | None = None
    total_depth_m: Decimal | None = None
    dip_degrees: Decimal | None = None
    azimuth_degrees: Decimal | None = None
    drilling_method: str | None = None


class DrillHoleService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, data: DrillHoleInput, *, registered_by: LabUser, actor_role: Role
    ) -> DrillHole:
        if actor_role not in BENCH_ROLES:
            raise InsufficientRoleError(
                f"{actor_role.value} may not register a drill hole; this needs one of "
                + ", ".join(sorted(role.value for role in BENCH_ROLES))
            )

        project = self._session.get(Project, data.project_id)
        if project is None:
            raise ProjectNotFoundError(f"no project with id {data.project_id}")

        problems: list[str] = []

        hole_id: str | None = None
        try:
            hole_id = canonical_hole_id(data.hole_id)
        except SampleIdError as exc:
            problems.append(str(exc))

        if hole_id is not None:
            existing = self._session.scalar(
                select(DrillHole).where(
                    DrillHole.project_id == project.id, DrillHole.hole_id == hole_id
                )
            )
            if existing is not None:
                problems.append(f"{project.name!r} already has a hole named {hole_id!r}")

        if problems:
            raise DrillHoleValidationError(problems)

        assert hole_id is not None  # guaranteed by the problems check above

        hole = DrillHole(
            project_id=project.id,
            hole_id=hole_id,
            easting=data.easting,
            northing=data.northing,
            elevation_m=data.elevation_m,
            utm_zone=data.utm_zone,
            total_depth_m=data.total_depth_m,
            dip_degrees=data.dip_degrees,
            azimuth_degrees=data.azimuth_degrees,
            drilling_method=data.drilling_method,
        )
        self._session.add(hole)
        self._session.flush()

        self._session.add(
            AuditEvent(
                table_name="drill_hole",
                record_id=hole.id,
                action="create",
                after={"project_id": project.id, "hole_id": hole.hole_id},
                actor_id=registered_by.id,
            )
        )
        return hole
