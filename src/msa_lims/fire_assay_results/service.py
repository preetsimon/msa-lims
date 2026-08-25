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

**A result may name the crucible it came from; when it does, the portion
weight is derived, not re-entered.** ``Crucible.sample_weight_g`` is recorded
when the technician physically weighs the charge (see ``batches/service.py``),
so once a crucible is named, trusting a second, freshly-typed weight would let
the result contradict its own provenance. The request therefore carries either
a crucible or a portion weight — never both — and the stored row always shows
which path produced it. A named crucible must belong to the same sample and
must have made it at least as far as cupellation: a bead does not exist before
cupellation, and a rejected fusion produces no bead to weigh. Result entry
does not itself advance the crucible's status — parting and weighing are
per-crucible measurements with their own future write path (PROGRESS.md), not
a side effect of typing the bead in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from msa_lims.db.models import AuditEvent, Crucible, FireAssayResult, LabUser, Sample
from msa_lims.domain.assay import gravimetric_grade
from msa_lims.domain.enums import (
    MAY_ENTER_RESULTS,
    AssayMethod,
    CrucibleStatus,
    Role,
    SampleStatus,
)
from msa_lims.domain.lifecycle import InsufficientRoleError
from msa_lims.domain.units import Unit
from msa_lims.domain.values import MeasuredValue

#: Crucible statuses a result may be entered against — the crucible has
#: produced a bead. Everything before ``CUPELLED`` means no bead exists yet;
#: ``REJECTED`` means the fusion failed and never will.
_BEAD_BEARING: frozenset[CrucibleStatus] = frozenset(
    {CrucibleStatus.CUPELLED, CrucibleStatus.PARTED, CrucibleStatus.WEIGHED}
)


class SampleNotFoundError(ValueError):
    """No sample with this id exists."""


class CrucibleNotFoundError(ValueError):
    """No crucible with this id exists.

    Defined here rather than in ``batches/service.py`` for the same reason
    :class:`SampleNotFoundError` lives here: ``batches/service.py`` already
    imports from this module (it raises ``SampleNotFoundError`` while
    charging), so importing this error from there would create an import
    cycle for no benefit the one-way dependency doesn't already give.
    """


class FireAssayResultValidationError(ValueError):
    """One or more problems with a fire assay result, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class FireAssayResultInput:
    sample_id: int
    gold_bead_mg: Decimal
    #: The portion assayed. Required when no crucible is named; refused when
    #: one is — the crucible's recorded charge is the portion, and a second,
    #: freshly-typed number could contradict it. See the module docstring.
    sample_weight_g: Decimal | None
    balance_sensitivity_mg: Decimal | None
    analysed_at: datetime
    notes: str | None = None
    #: Set both to correct an existing result; see the module docstring.
    supersedes_id: int | None = None
    superseded_reason: str | None = None
    #: Name this to tie the result to the crucible the sample was charged
    #: into; ``sample_weight_g`` must then be left unset.
    crucible_id: int | None = None


def current_result(session: Session, sample_id: int) -> FireAssayResult | None:
    """The row for this sample that nothing else supersedes, if any.

    Found by exclusion rather than a stored "is_current" flag: a row is
    current exactly when no other row's ``supersedes_id`` names it, so there
    is nothing to keep in sync when a new correction lands. Expressed as a
    LEFT JOIN / IS NULL anti-join against an aliased successor — the planner
    can use indexes on the join instead of materialising every superseded id
    in the table the way a NOT IN subquery would.

    Exported (not module-private) because certificate issuance needs the
    identical question answered the identical way — a certificate must
    freeze the *current* result at the moment it is signed, not just any
    result that happens to exist for the sample.
    """
    successor = aliased(FireAssayResult)
    return session.scalar(
        select(FireAssayResult)
        .outerjoin(successor, successor.supersedes_id == FireAssayResult.id)
        .where(FireAssayResult.sample_id == sample_id, successor.id.is_(None))
        .order_by(FireAssayResult.id.desc())
    )


def measured_value(result: FireAssayResult) -> MeasuredValue:
    """Reconstruct the domain value a stored row represents.

    The four ``au_*`` columns are how a :class:`MeasuredValue` is carried in
    the schema (see the ``FireAssayResult`` model docstring); this is the one
    place that turns them back into the object, so formatting — a censored
    value rendering as ``<0.01 g/t``, never as an empty or zero value — stays
    identical everywhere a result is displayed, on a certificate or anywhere
    else.
    """
    return MeasuredValue(
        unit=Unit(result.au_unit),
        value=result.au_value,
        detection_limit=result.au_detection_limit,
        censored=result.au_censored,
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

        current = current_result(self._session, sample.id)

        crucible, portion_weight, wiring_problems = self._resolve_portion(data, sample)
        problems = self._check_supersession(data, sample, current) + wiring_problems
        if problems:
            raise FireAssayResultValidationError(problems)
        # Guaranteed by _resolve_portion: a clean resolution without a named
        # crucible carries the caller's weight; one with a crucible carries
        # the crucible's recorded charge.
        assert portion_weight is not None

        # Not caught here: AssayCalculationError propagates and is mapped to
        # 422 globally, same as every other domain refusal in web/app.py.
        grade = gravimetric_grade(
            gold_bead_mg=data.gold_bead_mg,
            sample_weight_g=portion_weight,
            balance_sensitivity_mg=data.balance_sensitivity_mg,
        )

        result = FireAssayResult(
            sample_id=sample.id,
            method=AssayMethod.FIRE_ASSAY_GRAVIMETRIC,
            gold_bead_mg=data.gold_bead_mg,
            sample_weight_g=portion_weight,
            balance_sensitivity_mg=data.balance_sensitivity_mg,
            au_value=grade.value,
            au_detection_limit=grade.detection_limit,
            au_censored=grade.censored,
            au_unit=grade.unit.value,
            analyst_id=analyst.id,
            analysed_at=data.analysed_at,
            crucible_id=crucible.id if crucible is not None else None,
            supersedes_id=data.supersedes_id,
            superseded_reason=data.superseded_reason,
            notes=data.notes,
        )
        self._session.add(result)
        self._session.flush()

        after: dict[str, object] = {"sample_id": sample.id, "au": str(grade)}
        if crucible is not None:
            after["crucible_id"] = crucible.id
        self._session.add(
            AuditEvent(
                table_name="fire_assay_result",
                record_id=result.id,
                action="amend" if data.supersedes_id is not None else "create",
                after=after,
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

    def _resolve_portion(
        self, data: FireAssayResultInput, sample: Sample
    ) -> tuple[Crucible | None, Decimal | None, list[str]]:
        """The crucible this result came from and the portion it was charged
        with, plus every provenance problem found.

        Returns ``(None, weight, [])`` for direct entry — the caller's own
        ``sample_weight_g`` is the portion. A named crucible returns the
        crucible and its recorded charge; any refusal comes back in the
        problem list so it is reported alongside supersession problems rather
        than alone. An unknown crucible id is different in kind — like an
        unknown sample id, there is no interpretation to offer — so it raises
        :class:`CrucibleNotFoundError` directly.
        """
        if data.crucible_id is None:
            if data.sample_weight_g is None:
                return (
                    None,
                    None,
                    [
                        "sample_weight_g is required unless the result names the crucible "
                        "the sample was charged into"
                    ],
                )
            return None, data.sample_weight_g, []

        crucible = self._session.get(Crucible, data.crucible_id)
        if crucible is None:
            raise CrucibleNotFoundError(f"no crucible with id {data.crucible_id}")

        problems: list[str] = []
        if data.sample_weight_g is not None:
            problems.append(
                "name the crucible or weigh the portion, not both: once a crucible is "
                "named, its recorded charge is the portion assayed"
            )
        if crucible.sample_id != sample.id:
            problems.append(
                f"crucible #{crucible.id} was charged with sample #{crucible.sample_id}, "
                f"not #{sample.id}"
            )
        if crucible.status not in _BEAD_BEARING:
            problems.append(
                f"crucible #{crucible.id} is {crucible.status.value}; a bead exists only "
                "after cupellation"
            )
        return crucible, (None if problems else crucible.sample_weight_g), problems
