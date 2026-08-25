"""Submission intake: from a work order to persisted sample rows.

Everything upstream of this module is pure — :mod:`msa_lims.domain.sample_id`
parses labels and checks depth intervals with no session in sight. This is
where that pure work meets the database: the only place in this package that
knows a session exists.

The order of operations mirrors QC Sentinel's ingestion service, for the same
reason: **validate everything before writing anything.** A forty-sample
submission with one bad label should report that one label — and every other
problem in the batch — in a single response, not fail on write after already
having accepted the first thirty-nine.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from msa_lims.clients.service import ClientNotFoundError
from msa_lims.db.models import AuditEvent, Client, DrillHole, LabUser, Project, Sample, Submission
from msa_lims.domain.enums import Role, SampleStatus, SampleType
from msa_lims.domain.lifecycle import BENCH_ROLES, InsufficientRoleError
from msa_lims.domain.sample_id import (
    DepthInterval,
    SampleIdentity,
    SampleIdError,
    find_overlaps,
    parse_sample_id,
)

__all__ = [
    "ClientNotFoundError",
    "SampleInput",
    "SubmissionInput",
    "SubmissionService",
    "SubmissionValidationError",
]


class SubmissionValidationError(ValueError):
    """One or more sample rows could not be accepted.

    Carries every problem found, not just the first — the person entering a
    forty-sample submission wants the whole list in one pass. Mirrors
    :func:`msa_lims.domain.sample_id.find_overlaps`, which reports every
    conflicting pair rather than the first.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class SampleInput:
    sample_id: str
    sample_type: SampleType
    lithology_code: str | None = None
    alteration_code: str | None = None
    weight_received_g: Decimal | None = None
    easting: Decimal | None = None
    northing: Decimal | None = None
    elevation_m: Decimal | None = None
    comments: str | None = None


@dataclass(frozen=True, slots=True)
class SubmissionInput:
    client_id: int
    project_id: int | None
    client_reference: str | None
    purchase_order: str | None
    received_at: datetime
    declared_sample_count: int | None
    rush: bool
    requested_tat_days: int | None
    comments: str | None
    samples: tuple[SampleInput, ...]


class SubmissionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, data: SubmissionInput, *, received_by: LabUser, actor_role: Role
    ) -> Submission:
        """Validate the whole batch, then write the submission and its samples.

        ``actor_role`` is read fresh from the current request rather than from
        ``received_by.role`` — see :func:`msa_lims.web.deps.current_lab_user` —
        so this check can never be satisfied by a stored role that has drifted
        from what the caller currently holds.
        """
        if actor_role not in BENCH_ROLES:
            raise InsufficientRoleError(
                f"{actor_role.value} may not receive a submission; this needs one of "
                + ", ".join(sorted(role.value for role in BENCH_ROLES))
            )

        client = self._session.get(Client, data.client_id)
        if client is None:
            raise ClientNotFoundError(f"no client with id {data.client_id}")

        problems: list[str] = []

        project: Project | None = None
        if data.project_id is not None:
            project = self._session.get(Project, data.project_id)
            if project is None:
                problems.append(f"no project with id {data.project_id}")
            elif project.client_id != client.id:
                problems.append(
                    f"project {data.project_id} belongs to a different client, not {client.name!r}"
                )

        parsed, parse_problems = self._parse_samples(data.samples)
        problems.extend(parse_problems)
        problems.extend(self._check_duplicate_labels(parsed))
        problems.extend(self._check_already_received(parsed))

        hole_cache: dict[str, DrillHole] = {}
        problems.extend(self._resolve_drill_holes(parsed, project, hole_cache))
        problems.extend(self._check_overlaps(parsed, hole_cache))

        if problems:
            raise SubmissionValidationError(problems)

        submission = Submission(
            submission_number=self._allocate_number(data.received_at),
            client_id=client.id,
            project_id=project.id if project else None,
            client_reference=data.client_reference,
            purchase_order=data.purchase_order,
            received_at=data.received_at,
            received_by_id=received_by.id,
            declared_sample_count=data.declared_sample_count,
            rush=data.rush,
            requested_tat_days=data.requested_tat_days,
            comments=data.comments,
        )
        self._session.add(submission)
        self._session.flush()  # need submission.id for the samples and the audit event

        self._audit(
            "submission",
            submission.id,
            received_by,
            after={
                "submission_number": submission.submission_number,
                "client_id": client.id,
                "sample_count": len(parsed),
            },
        )

        samples: list[Sample] = []
        for item, identity in parsed:
            hole = hole_cache.get(identity.hole_id) if identity.is_drill_sample else None
            sample = Sample(
                sample_id=identity.raw,
                submission_id=submission.id,
                drill_hole_id=hole.id if hole else None,
                sample_type=item.sample_type,
                status=SampleStatus.RECEIVED,
                from_depth_m=identity.interval.from_depth_m if identity.interval else None,
                to_depth_m=identity.interval.to_depth_m if identity.interval else None,
                easting=item.easting,
                northing=item.northing,
                elevation_m=item.elevation_m,
                lithology_code=item.lithology_code,
                alteration_code=item.alteration_code,
                weight_received_g=item.weight_received_g,
                comments=item.comments,
            )
            self._session.add(sample)
            self._session.flush()
            self._audit(
                "sample",
                sample.id,
                received_by,
                after={"sample_id": sample.sample_id, "submission_id": submission.id},
            )
            samples.append(sample)

        submission.samples = samples
        return submission

    # -- validation -----------------------------------------------------

    def _parse_samples(
        self, items: tuple[SampleInput, ...]
    ) -> tuple[list[tuple[SampleInput, SampleIdentity]], list[str]]:
        parsed: list[tuple[SampleInput, SampleIdentity]] = []
        problems: list[str] = []
        for item in items:
            try:
                identity = parse_sample_id(item.sample_id)
            except SampleIdError as exc:
                problems.append(str(exc))
                continue
            parsed.append((item, identity))
        return parsed, problems

    def _check_duplicate_labels(
        self, parsed: list[tuple[SampleInput, SampleIdentity]]
    ) -> list[str]:
        counts = Counter(identity.raw for _, identity in parsed)
        return [
            f"sample id {label!r} appears more than once in this submission"
            for label, count in sorted(counts.items())
            if count > 1
        ]

    def _check_already_received(
        self, parsed: list[tuple[SampleInput, SampleIdentity]]
    ) -> list[str]:
        labels = {identity.raw for _, identity in parsed}
        if not labels:
            return []
        existing = self._session.scalars(
            select(Sample.sample_id).where(Sample.sample_id.in_(labels))
        )
        return [f"sample id {label!r} has already been received" for label in sorted(existing)]

    def _resolve_drill_holes(
        self,
        parsed: list[tuple[SampleInput, SampleIdentity]],
        project: Project | None,
        cache: dict[str, DrillHole],
    ) -> list[str]:
        """Resolve every drill sample's hole, one problem per distinct failure.

        Grouped by hole first: a batch of six samples from the same
        unregistered hole is one problem the front desk has to fix, not six
        copies of it.
        """
        problems: list[str] = []
        by_hole: dict[str, list[str]] = defaultdict(list)
        for _item, identity in parsed:
            if identity.is_drill_sample:
                by_hole[identity.hole_id].append(identity.raw)

        if not by_hole:
            return problems

        if project is None:
            labels = sorted(label for labels in by_hole.values() for label in labels)
            problems.append(
                f"drill sample(s) {', '.join(repr(label) for label in labels)} name no "
                "project on the submission; a drill hole must belong to a project"
            )
            return problems

        for hole_id in sorted(by_hole):
            if hole_id in cache:
                continue
            hole = self._session.scalar(
                select(DrillHole).where(
                    DrillHole.project_id == project.id, DrillHole.hole_id == hole_id
                )
            )
            if hole is None:
                problems.append(
                    f"no drill hole {hole_id!r} is registered under project {project.name!r}; "
                    "register the hole before submitting samples from it"
                )
                continue
            cache[hole_id] = hole
        return problems

    def _check_overlaps(
        self,
        parsed: list[tuple[SampleInput, SampleIdentity]],
        hole_cache: dict[str, DrillHole],
    ) -> list[str]:
        new_labels: set[str] = set()
        by_hole: dict[str, list[tuple[str, DepthInterval]]] = defaultdict(list)
        for _item, identity in parsed:
            if identity.interval is None:
                continue
            hole = hole_cache.get(identity.hole_id)
            if hole is None:
                continue  # already reported as a missing-hole problem
            by_hole[identity.hole_id].append((identity.raw, identity.interval))
            new_labels.add(identity.raw)

        problems: list[str] = []
        for hole_id, new_intervals in by_hole.items():
            hole = hole_cache[hole_id]
            existing_rows = self._session.execute(
                select(Sample.sample_id, Sample.from_depth_m, Sample.to_depth_m).where(
                    Sample.drill_hole_id == hole.id
                )
            ).all()
            existing_intervals = [
                (row.sample_id, DepthInterval(row.from_depth_m, row.to_depth_m))
                for row in existing_rows
            ]
            conflicts = find_overlaps(existing_intervals + new_intervals)
            for label_a, label_b in conflicts:
                if label_a in new_labels or label_b in new_labels:
                    problems.append(f"{label_a!r} and {label_b!r} overlap in {hole_id}")
        return problems

    # -- writes -----------------------------------------------------------

    def _allocate_number(self, received_at: datetime) -> str:
        """A submission number in the shape ``SUB-2026-0841``.

        **Provisional.** The real numbering convention is an open question —
        see PROGRESS.md — and this is a placeholder good enough to demonstrate
        the shape of the system: a per-year count of existing submissions,
        incremented. It is correct only under the single-writer assumption
        already documented for this schema's primary keys (see
        :data:`msa_lims.db.base.IdPk`); it is not safe against two concurrent
        submissions racing for the same number, which a real front desk would
        eventually do.
        """
        prefix = f"SUB-{received_at.year}-"
        count = (
            self._session.scalar(
                select(func.count())
                .select_from(Submission)
                .where(Submission.submission_number.like(f"{prefix}%"))
            )
            or 0
        )
        return f"{prefix}{count + 1:04d}"

    def _audit(
        self,
        table_name: str,
        record_id: int,
        actor: LabUser,
        *,
        after: dict[str, object] | None = None,
    ) -> None:
        self._session.add(
            AuditEvent(
                table_name=table_name,
                record_id=record_id,
                action="create",
                after=after,
                actor_id=actor.id,
            )
        )
