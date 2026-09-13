"""Multi-element ICP result entry: from an instrument export to stored, append-only grades.

**One row per element per sample per digest method.** An ICP-MS run on a
dissolved sample produces 30-50+ element concentrations; each is stored as its
own row in ``multi_element_result``, with the same append-only, supersession-
chain pattern as ``fire_assay_result``. The grade is the *final* mass fraction
(ppm or ppb) in the solid sample — not the raw instrument reading in mg/L —
because the certificate quotes this number and it must be the one the analyst
verified, not an intermediate the code computed.

**Bulk import, not one-at-a-time.** An instrument exports a CSV with dozens of
elements per sample; the import endpoint accepts the whole run at once. Each
element is validated independently (known element, non-negative grade, valid
unit), and all pass or all fail — a partial write would leave the sample with
half its elements assayed, which is worse than refusing the whole batch.

**Digest method is mandatory.** Unlike fire assay (which is always fusion),
multi-element work genuinely varies: aqua regia is partial, four-acid is total,
peroxide fusion is something else again. The certificate must name which was
used, so the service refuses to store a result without one. Two digests of the
same sample produce two independent rows for the same element — different
statement, both valid, both on record.

**Lifecycle gate.** Entering the first multi-element result for a sample calls
``check_transition`` for ``IN_ASSAY → ASSAYED``, exactly as fire assay does.
A sample that is still ``RECEIVED``, ``IN_PREP``, ``READY_FOR_ASSAY``, or
``REJECTED`` is refused with the real ``TransitionNotAllowedError`` (409),
naming what it needs to reach first. Superseding an existing element reading
does not touch sample status — the sample was already ``ASSAYED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import LabUser, MultiElementResult, Sample
from msa_lims.domain.enums import (
    MAY_ENTER_RESULTS,
    DigestMethod,
    Element,
    Role,
    SampleStatus,
)
from msa_lims.domain.lifecycle import InsufficientRoleError, check_transition
from msa_lims.domain.units import Dimension, Unit, dimension_of
from msa_lims.fire_assay_results.service import SampleNotFoundError


class MultiElementResultError(ValueError):
    """One or more problems with a multi-element import, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class ElementResult:
    """One element's grade, as entered by the analyst."""

    element: Element
    grade_value: Decimal
    grade_unit: Unit = Unit.PPM
    detection_limit: Decimal | None = None


@dataclass(frozen=True, slots=True)
class MultiElementImportInput:
    """A full ICP run's worth of elements for one sample."""

    sample_id: int
    digest_method: DigestMethod
    method_notes: str | None
    analysed_at: datetime
    results: list[ElementResult]


def current_results(session: Session, sample_id: int) -> list[MultiElementResult]:
    """The un-superseded head per element for this sample.

    For each (element, digest_method) triple, returns the row nothing else
    supersedes — the most recent reading. Found by the same anti-join
    pattern as ``fire_assay_results.service.current_result``.
    """
    successor = aliased(MultiElementResult)
    return list(
        session.scalars(
            select(MultiElementResult)
            .outerjoin(successor, successor.supersedes_id == MultiElementResult.id)
            .where(
                MultiElementResult.sample_id == sample_id,
                successor.id.is_(None),
            )
            .order_by(MultiElementResult.element, MultiElementResult.digest_method)
        )
    )


class MultiElementService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def import_results(
        self,
        data: MultiElementImportInput,
        *,
        analyst: LabUser,
        actor_role: Role,
    ) -> list[MultiElementResult]:
        """Bulk-insert one ICP run's worth of element results for a sample.

        All elements are validated before any are written: known element enum,
        non-negative grade, valid unit, no duplicate elements in the same
        request. The whole batch succeeds or the whole batch is refused.
        """
        if actor_role not in MAY_ENTER_RESULTS:
            raise InsufficientRoleError(
                f"{actor_role.value} may not enter a multi-element result; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_ENTER_RESULTS))
            )

        sample = self._session.get(Sample, data.sample_id)
        if sample is None:
            raise SampleNotFoundError(f"no sample with id {data.sample_id}")

        problems = self._validate_import(data, sample)
        if problems:
            raise MultiElementResultError(problems)

        # Lifecycle gate: move the sample to ASSAYED if not already there.
        # A REPORTED sample is refused by check_transition itself, naming
        # the amended-certificate remedy. An already-ASSAYED sample importing
        # a second digest is a no-op on status.
        if sample.status is not SampleStatus.ASSAYED:
            check_transition(
                source=sample.status,
                target=SampleStatus.ASSAYED,
                sample_type=sample.sample_type,
                role=actor_role,
            )
            sample.status = SampleStatus.ASSAYED

        rows: list[MultiElementResult] = []
        for item in data.results:
            # Censoring: when a detection limit is present and the value is
            # at or below it, store the limit and flag the row as censored.
            censored = (
                item.detection_limit is not None and item.grade_value <= item.detection_limit
            )
            stored_value = item.detection_limit if censored else item.grade_value

            grade = MultiElementResult(
                sample_id=sample.id,
                element=item.element,
                grade_value=stored_value,
                grade_unit=item.grade_unit.value,
                detection_limit=item.detection_limit,
                grade_censored=censored,
                digest_method=data.digest_method,
                method_notes=data.method_notes,
                analyst_id=analyst.id,
                analysed_at=data.analysed_at,
            )
            self._session.add(grade)
            self._session.flush()

            record_audit_event(
                self._session,
                table_name="multi_element_result",
                record_id=grade.id,
                action="create",
                actor_id=analyst.id,
                after={
                    "sample_id": sample.id,
                    "element": item.element.value,
                    "grade": f"{item.grade_value} {item.grade_unit}",
                    "digest_method": data.digest_method.value,
                },
            )
            rows.append(grade)

        return rows

    def supersede(
        self,
        *,
        sample_id: int,
        element: Element,
        digest_method: DigestMethod,
        new_value: Decimal,
        new_unit: Unit = Unit.PPM,
        detection_limit: Decimal | None = None,
        analysed_at: datetime,
        method_notes: str | None = None,
        reason: str,
        analyst: LabUser,
        actor_role: Role,
    ) -> MultiElementResult:
        """Correct a single element reading with a new row in the chain.

        The element and digest must already have a current reading for this
        sample; the new row supersedes it with a required reason.
        """
        if actor_role not in MAY_ENTER_RESULTS:
            raise InsufficientRoleError(
                f"{actor_role.value} may not amend a multi-element result; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_ENTER_RESULTS))
            )

        sample = self._session.get(Sample, sample_id)
        if sample is None:
            raise SampleNotFoundError(f"no sample with id {sample_id}")

        successor = aliased(MultiElementResult)
        current = self._session.scalar(
            select(MultiElementResult)
            .outerjoin(successor, successor.supersedes_id == MultiElementResult.id)
            .where(
                MultiElementResult.sample_id == sample_id,
                MultiElementResult.element == element,
                MultiElementResult.digest_method == digest_method,
                successor.id.is_(None),
            )
        )
        if current is None:
            raise MultiElementResultError(
                [
                    f"no current {element.value} reading for sample {sample.sample_id!r} "
                    f"with {digest_method.value} digest; nothing to supersede"
                ]
            )

        if new_value < 0:
            raise MultiElementResultError([f"grade cannot be negative: {new_value}"])

        # Censoring: when a detection limit is present and the value is
        # at or below it, store the limit and flag the row as censored.
        censored = detection_limit is not None and new_value <= detection_limit
        stored_value = detection_limit if censored else new_value

        new_row = MultiElementResult(
            sample_id=sample.id,
            element=element,
            grade_value=stored_value,
            grade_unit=new_unit.value,
            detection_limit=detection_limit,
            grade_censored=censored,
            digest_method=digest_method,
            method_notes=method_notes,
            analyst_id=analyst.id,
            analysed_at=analysed_at,
            supersedes_id=current.id,
            superseded_reason=reason,
        )
        self._session.add(new_row)
        self._session.flush()

        record_audit_event(
            self._session,
            table_name="multi_element_result",
            record_id=new_row.id,
            action="amend",
            actor_id=analyst.id,
            after={
                "sample_id": sample.id,
                "element": element.value,
                "grade": f"{new_value} {new_unit}",
                "digest_method": digest_method.value,
            },
            reason=reason,
        )

        return new_row

    def _validate_import(self, data: MultiElementImportInput, sample: Sample) -> list[str]:
        """Check the whole batch before writing anything."""
        problems: list[str] = []

        if not data.results:
            problems.append("no element results provided")

        seen: set[Element] = set()
        for i, item in enumerate(data.results):
            if item.element in seen:
                problems.append(f"duplicate element {item.element.value} at position {i}")
            seen.add(item.element)

            if item.grade_value < 0:
                problems.append(
                    f"element {item.element.value}: grade cannot be negative ({item.grade_value})"
                )

            if dimension_of(item.grade_unit) is not Dimension.MASS_FRACTION:
                problems.append(
                    f"element {item.element.value}: {item.grade_unit.value} measures "
                    f"{dimension_of(item.grade_unit).value}, but a grade is a mass fraction"
                )

        return problems
