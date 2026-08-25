"""Fire assay result entry: from a weighed bead to a stored, append-only grade.

**Scope note.** Only the gravimetric method is entered here. AAS and ICP-MS
read a concentration off a calibration curve, not a bead weight, and need a
different input shape entirely when they are built. The stored row's
``method`` is always ``FIRE_ASSAY_GRAVIMETRIC`` for that reason — there is no
method parameter to get wrong, and no dead branch pretending to validate
methods nothing here implements.

**Results are append-only.** A corrected result is a new row whose
``supersedes_id`` points at the one it corrects, with a required reason —
never an ``UPDATE``. Only the row nothing else supersedes is *current* for a
sample:

* Entering a brand-new (non-superseding) result against a sample that already
  has a current one is refused. Correct it explicitly instead.
* Superseding a row that is not currently the head of the chain is refused —
  mirrors QC Sentinel's rule against double-replacement in a re-assay chain.
  Only the current result can be corrected; correcting a correction means
  superseding *that* row, not the one it replaced.

**Entering a sample's first result also moves it to ``ASSAYED``.** Later
phases will insert prep and furnace-batch stages between ``RECEIVED`` and this
point (see PROGRESS.md); until they exist, moving straight from whatever
status the sample is in is the honest reflection of what this system currently
tracks, not a shortcut hidden behind the full lifecycle machinery in
``domain/lifecycle.py``. A superseding result does not touch sample status —
the sample was already ``ASSAYED``, and correcting a number is a different act
from re-running the sample through the furnace (see
``domain.lifecycle``'s ``ASSAYED -> READY_FOR_ASSAY`` re-assay transition).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, FireAssayResult, LabUser, Sample
from msa_lims.domain.assay import gravimetric_grade
from msa_lims.domain.enums import MAY_ENTER_RESULTS, AssayMethod, Role, SampleStatus
from msa_lims.domain.lifecycle import InsufficientRoleError


class SampleNotFoundError(ValueError):
    """No sample with this id exists."""


class FireAssayResultValidationError(ValueError):
    """One or more problems with a fire assay result, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class FireAssayResultInput:
    sample_id: int
    gold_bead_mg: Decimal
    sample_weight_g: Decimal
    balance_sensitivity_mg: Decimal | None
    analysed_at: datetime
    notes: str | None = None
    #: Set both to correct an existing result; see the module docstring.
    supersedes_id: int | None = None
    superseded_reason: str | None = None


def _current_result(session: Session, sample_id: int) -> FireAssayResult | None:
    """The row for this sample that nothing else supersedes, if any.

    Found by exclusion rather than a stored "is_current" flag: a row is
    current exactly when no other row's ``supersedes_id`` names it, so there
    is nothing to keep in sync when a new correction lands.
    """
    superseded_ids = select(FireAssayResult.supersedes_id).where(
        FireAssayResult.supersedes_id.is_not(None)
    )
    return session.scalar(
        select(FireAssayResult)
        .where(FireAssayResult.sample_id == sample_id, FireAssayResult.id.not_in(superseded_ids))
        .order_by(FireAssayResult.id.desc())
    )


class FireAssayResultService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, data: FireAssayResultInput, *, analyst: LabUser, actor_role: Role
    ) -> FireAssayResult:
        if actor_role not in MAY_ENTER_RESULTS:
            raise InsufficientRoleError(
                f"{actor_role.value} may not enter a fire assay result; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_ENTER_RESULTS))
            )

        sample = self._session.get(Sample, data.sample_id)
        if sample is None:
            raise SampleNotFoundError(f"no sample with id {data.sample_id}")

        current = _current_result(self._session, sample.id)
        problems = self._check_supersession(data, sample, current)
        if problems:
            raise FireAssayResultValidationError(problems)

        # Not caught here: AssayCalculationError propagates and is mapped to
        # 422 globally, same as every other domain refusal in web/app.py.
        grade = gravimetric_grade(
            gold_bead_mg=data.gold_bead_mg,
            sample_weight_g=data.sample_weight_g,
            balance_sensitivity_mg=data.balance_sensitivity_mg,
        )

        result = FireAssayResult(
            sample_id=sample.id,
            method=AssayMethod.FIRE_ASSAY_GRAVIMETRIC,
            gold_bead_mg=data.gold_bead_mg,
            sample_weight_g=data.sample_weight_g,
            balance_sensitivity_mg=data.balance_sensitivity_mg,
            au_value=grade.value,
            au_detection_limit=grade.detection_limit,
            au_censored=grade.censored,
            au_unit=grade.unit.value,
            analyst_id=analyst.id,
            analysed_at=data.analysed_at,
            supersedes_id=data.supersedes_id,
            superseded_reason=data.superseded_reason,
            notes=data.notes,
        )
        self._session.add(result)
        self._session.flush()

        self._session.add(
            AuditEvent(
                table_name="fire_assay_result",
                record_id=result.id,
                action="amend" if data.supersedes_id is not None else "create",
                after={"sample_id": sample.id, "au": str(grade)},
                reason=data.superseded_reason,
                actor_id=analyst.id,
            )
        )

        if data.supersedes_id is None:
            sample.status = SampleStatus.ASSAYED

        return result

    def _check_supersession(
        self,
        data: FireAssayResultInput,
        sample: Sample,
        current: FireAssayResult | None,
    ) -> list[str]:
        problems: list[str] = []

        if data.supersedes_id is None:
            if current is not None:
                problems.append(
                    f"sample {sample.sample_id!r} already has a result (#{current.id}); "
                    "supersede it explicitly to correct it rather than entering a new one"
                )
            if sample.status is SampleStatus.REJECTED:
                problems.append(f"sample {sample.sample_id!r} was rejected and cannot be assayed")
            return problems

        if current is None or current.id != data.supersedes_id:
            problems.append(
                f"result #{data.supersedes_id} is not the current result for sample "
                f"{sample.sample_id!r}; only the current result can be superseded"
            )
        if not (data.superseded_reason and data.superseded_reason.strip()):
            problems.append("superseding a result requires a reason")
        return problems
