"""Fire assay result entry: from a bench measurement to a stored, append-only grade.

**Two finishes, two entry points, one chain.** A fusion can be finished by
weighing the parted bead (gravimetric) or by dissolving it and reading the
solution on an AAS or ICP-MS. They take completely different measurements, so
they have separate inputs (:class:`FireAssayResultInput`,
:class:`SolutionFinishInput`), separate service methods, and separate
endpoints — no ``method`` parameter switching over branches, which is what the
original scope note here promised when only the gravimetric path existed.

What they deliberately *share* is everything downstream of the calculation:
one table, one supersession chain, one answer to "what is this sample's
current result." That sharing is not incidental. The workflow that most needs
it runs between the two finishes — a solution result that saturates gets
re-run gravimetrically, and the referee result supersedes the first. Two
chains would make "the current result" two questions with two answers, and a
certificate could freeze one while the other held a newer correction.

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

**Entering a sample's first result genuinely calls
``domain.lifecycle.check_transition`` for ``IN_ASSAY -> ASSAYED`` — not a
bypass.** Earlier phases could not do this honestly: nothing existed yet to
move a sample to ``IN_ASSAY``, so any non-``REJECTED`` sample was accepted
with a hand-rolled guard and this docstring said so. As of
``sample_lifecycle/service.py`` and ``batches/service.py``'s furnace-charging
rewiring, a sample reaches ``IN_ASSAY`` for real, and entering a result now
requires it — the same way charging a crucible requires the real
``READY_FOR_ASSAY``. A sample still ``RECEIVED``, ``IN_PREP``,
``READY_FOR_ASSAY``, or ``REJECTED`` is refused with the real
``TransitionNotAllowedError`` (**409**), naming what it needs to reach
first, rather than the old generic **422**. The check is skipped only when
the sample already has a current result to point at instead — "supersede
result #N" is a more useful refusal than "the sample is already assayed" for
the identical underlying fact. A superseding result does not touch sample
status at all — the sample was already ``ASSAYED``, and correcting a number
is a different act from re-running the sample through the furnace (see
``domain.lifecycle``'s ``ASSAYED -> READY_FOR_ASSAY`` re-assay transition).

**A result may name the crucible it came from; when it does, its numbers are
derived, not re-entered.** ``Crucible.sample_weight_g`` is recorded when the
technician physically weighs the charge and ``Crucible.gold_bead_mg`` when
the parted bead reaches the balance (see ``batches/service.py``), so once a
weighed crucible is named, trusting freshly-typed copies would let the result
contradict its own provenance. The request therefore carries either a
crucible or raw weighings — per number, never both — and the stored row always
shows which path produced it. A named crucible must belong to the same sample
and must have made it at least as far as cupellation: a bead does not exist
before cupellation, and a rejected fusion produces no bead to weigh. A QC
insertion holds no sample at all, so it is refused here regardless of stage —
its bead is judged by QC Sentinel on export (Phase 5), never entered as a
sample's result. A crucible that has been *weighed* supplies the bead itself;
one still only cupelled or parted takes a typed bead, because nothing on
record can be read back yet. Result entry does not itself advance the
crucible's status — parting and weighing are recorded at the bench by their
own write paths, not as a side effect of typing the bead in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import Crucible, FireAssayResult, LabUser, Sample
from msa_lims.domain.assay import gravimetric_grade, solution_finish_grade
from msa_lims.domain.enums import (
    MAY_ENTER_RESULTS,
    AssayMethod,
    CrucibleStatus,
    Role,
    SampleStatus,
)
from msa_lims.domain.lifecycle import InsufficientRoleError, check_transition
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
    #: The bead weight after parting — gold alone. Required unless the named
    #: crucible has already been weighed, in which case its recorded bead is
    #: used and this must be left unset. See the module docstring.
    gold_bead_mg: Decimal | None
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


@dataclass(frozen=True, slots=True)
class SolutionFinishInput:
    """An AAS or ICP-MS finish: what the instrument read, and out of what.

    Shares ``sample_weight_g``'s meaning with :class:`FireAssayResultInput`
    (the portion assayed, derived from the crucible when one is named) and
    nothing else. There is no bead weight here — the bead was dissolved, which
    is the whole point of this finish — and correspondingly no
    ``balance_sensitivity_mg``: the detection limit of a solution finish is the
    method's, in the units the instrument reads, not a balance's.
    """

    sample_id: int
    method: AssayMethod
    #: What the instrument read, in ``concentration_unit``.
    concentration: Decimal
    #: Must measure mass concentration (mg/L, µg/L) — see
    #: ``domain.assay.solution_finish_grade`` for why ppm is refused here.
    concentration_unit: Unit
    #: The volume the dissolved bead was made up to.
    solution_volume_ml: Decimal
    #: The portion assayed. Required when no crucible is named; refused when
    #: one is, exactly as on the gravimetric path.
    sample_weight_g: Decimal | None
    analysed_at: datetime
    #: The method's detection limit, in ``concentration_unit``.
    detection_limit: Decimal | None = None
    #: The method's top standard, in ``concentration_unit``. Not stored — it
    #: gates whether the reading is a measurement at all. See the model.
    upper_calibration_limit: Decimal | None = None
    notes: str | None = None
    supersedes_id: int | None = None
    superseded_reason: str | None = None
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

    def _admit(
        self,
        *,
        sample_id: int,
        supersedes_id: int | None,
        actor_role: Role,
    ) -> tuple[Sample, FireAssayResult | None]:
        """The checks every result entry passes before its own measurements
        are looked at, whichever finish produced them.

        Returns the sample and its current result so the caller does not
        re-query either. Shared rather than duplicated per finish because
        every one of these rules is about *a result existing for this sample*,
        which is a fact about the chain, not about how the bead was read.
        """
        if actor_role not in MAY_ENTER_RESULTS:
            raise InsufficientRoleError(
                f"{actor_role.value} may not enter a fire assay result; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_ENTER_RESULTS))
            )

        sample = self._session.get(Sample, sample_id)
        if sample is None:
            raise SampleNotFoundError(f"no sample with id {sample_id}")

        current = current_result(self._session, sample.id)

        if supersedes_id is None and current is None:
            # The real transition, not a bypass — see the module docstring.
            # Raises TransitionNotAllowedError (409) for a sample not yet
            # IN_ASSAY; InsufficientRoleError cannot actually trigger here
            # since this transition's allowed_roles (BENCH_ROLES) is a
            # superset of MAY_ENTER_RESULTS, already checked above, but is
            # not caught so a future change to either role set fails loudly
            # instead of silently.
            check_transition(
                source=sample.status,
                target=SampleStatus.ASSAYED,
                sample_type=sample.sample_type,
                role=actor_role,
            )

        return sample, current

    def _record(
        self,
        result: FireAssayResult,
        *,
        sample: Sample,
        grade: MeasuredValue,
        crucible: Crucible | None,
        analyst: LabUser,
        supersedes_id: int | None,
        superseded_reason: str | None,
    ) -> FireAssayResult:
        """Persist a built row, audit it, and move the sample if it is the
        sample's first result. Identical for both finishes: what the audit
        trail and the lifecycle care about is that a grade now exists, not
        which instrument produced it."""
        self._session.add(result)
        self._session.flush()

        after: dict[str, object] = {
            "sample_id": sample.id,
            "au": str(grade),
            "method": result.method.value,
        }
        if crucible is not None:
            after["crucible_id"] = crucible.id
        record_audit_event(
            self._session,
            table_name="fire_assay_result",
            record_id=result.id,
            action="amend" if supersedes_id is not None else "create",
            actor_id=analyst.id,
            after=after,
            reason=superseded_reason,
        )

        if supersedes_id is None:
            sample.status = SampleStatus.ASSAYED

        return result

    def create(
        self, data: FireAssayResultInput, *, analyst: LabUser, actor_role: Role
    ) -> FireAssayResult:
        sample, current = self._admit(
            sample_id=data.sample_id,
            supersedes_id=data.supersedes_id,
            actor_role=actor_role,
        )

        crucible, portion_weight, bead_weight, wiring_problems = self._resolve_weighing(
            data, sample
        )
        problems = self._check_supersession(data, sample, current) + wiring_problems
        if problems:
            raise FireAssayResultValidationError(problems)
        # Guaranteed by _resolve_weighing: a clean resolution always carries
        # both numbers — the caller's typed weighings on the direct path, or
        # the crucible's recorded charge and bead on the wired one.
        assert portion_weight is not None
        assert bead_weight is not None

        # Not caught here: AssayCalculationError propagates and is mapped to
        # 422 globally, same as every other domain refusal in web/app.py.
        grade = gravimetric_grade(
            gold_bead_mg=bead_weight,
            sample_weight_g=portion_weight,
            balance_sensitivity_mg=data.balance_sensitivity_mg,
        )

        result = FireAssayResult(
            sample_id=sample.id,
            method=AssayMethod.FIRE_ASSAY_GRAVIMETRIC,
            gold_bead_mg=bead_weight,
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
        return self._record(
            result,
            sample=sample,
            grade=grade,
            crucible=crucible,
            analyst=analyst,
            supersedes_id=data.supersedes_id,
            superseded_reason=data.superseded_reason,
        )

    def create_solution_finish(
        self, data: SolutionFinishInput, *, analyst: LabUser, actor_role: Role
    ) -> FireAssayResult:
        """Enter an AAS or ICP-MS finish — a concentration, not a bead weight.

        The gravimetric path's crucible wiring applies here too, minus the
        bead: a named crucible still supplies the portion assayed and still
        has to be this sample's and to have produced a bead. What it cannot
        supply is the reading, because the bead left the crucible for a flask
        and an instrument.
        """
        if data.method is AssayMethod.FIRE_ASSAY_GRAVIMETRIC:
            # Reachable only by a direct service caller — the route's schema
            # offers the two solution methods and nothing else. Refused rather
            # than silently written, because the row it would build violates
            # the table's own CHECK constraints anyway; failing here names why.
            raise FireAssayResultValidationError(
                [
                    "the gravimetric finish weighs a bead rather than reading a solution; "
                    "enter it through create() instead"
                ]
            )

        sample, current = self._admit(
            sample_id=data.sample_id,
            supersedes_id=data.supersedes_id,
            actor_role=actor_role,
        )

        crucible, portion_weight, wiring_problems = self._resolve_portion(data, sample)
        problems = self._check_supersession(data, sample, current) + wiring_problems
        if problems:
            raise FireAssayResultValidationError(problems)
        assert portion_weight is not None  # guaranteed by a clean _resolve_portion

        # AssayCalculationError and the unit errors propagate to 422 globally,
        # same as the gravimetric path.
        grade = solution_finish_grade(
            concentration=data.concentration,
            concentration_unit=data.concentration_unit,
            solution_volume_ml=data.solution_volume_ml,
            sample_weight_g=portion_weight,
            detection_limit=data.detection_limit,
            upper_calibration_limit=data.upper_calibration_limit,
        )

        result = FireAssayResult(
            sample_id=sample.id,
            method=data.method,
            gold_bead_mg=None,
            sample_weight_g=portion_weight,
            solution_concentration=data.concentration,
            solution_concentration_unit=data.concentration_unit.value,
            solution_volume_ml=data.solution_volume_ml,
            solution_detection_limit=data.detection_limit,
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
        return self._record(
            result,
            sample=sample,
            grade=grade,
            crucible=crucible,
            analyst=analyst,
            supersedes_id=data.supersedes_id,
            superseded_reason=data.superseded_reason,
        )

    def _check_supersession(
        self,
        data: FireAssayResultInput | SolutionFinishInput,
        sample: Sample,
        current: FireAssayResult | None,
    ) -> list[str]:
        """Whether this row may join the sample's chain, and where.

        Takes either finish's input because it reads only the two supersession
        fields they have in common — and because the rule genuinely spans
        them: superseding a saturated AAS result with a gravimetric re-assay
        is the same chain operation as correcting a mistyped bead weight.
        """
        problems: list[str] = []

        if data.supersedes_id is None:
            if current is not None:
                problems.append(
                    f"sample {sample.sample_id!r} already has a result (#{current.id}); "
                    "supersede it explicitly to correct it rather than entering a new one"
                )
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
        self, data: SolutionFinishInput, sample: Sample
    ) -> tuple[Crucible | None, Decimal | None, list[str]]:
        """The crucible a solution finish came from and the portion it
        assayed, plus every provenance problem found.

        The same rules as :meth:`_resolve_weighing`'s portion half — a named
        crucible's recorded charge *is* the portion, so a second typed one is
        refused — and none of its bead half. A weighed crucible is not
        special here: its recorded bead belongs to a gravimetric finish that
        was performed instead of this one, so reading it back would attach a
        weighing to a result the instrument produced.
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
            if crucible.sample_id is None:
                problems.append(
                    f"crucible #{crucible.id} holds a QC material (#{crucible.qc_material_id}), "
                    f"not sample #{sample.id}; results are entered for sample crucibles only"
                )
            else:
                problems.append(
                    f"crucible #{crucible.id} was charged with sample #{crucible.sample_id}, "
                    f"not #{sample.id}"
                )
        if crucible.status not in _BEAD_BEARING:
            problems.append(
                f"crucible #{crucible.id} is {crucible.status.value}; a bead exists only "
                "after cupellation"
            )

        return crucible, None if problems else crucible.sample_weight_g, problems

    def _resolve_weighing(
        self, data: FireAssayResultInput, sample: Sample
    ) -> tuple[Crucible | None, Decimal | None, Decimal | None, list[str]]:
        """The crucible this result came from and the two weighings it stands
        for — the portion charged and the bead recovered — plus every
        provenance problem found.

        Direct entry (no crucible named) returns ``(None, weight, bead,
        [])`` from the caller's own numbers. A named crucible derives the
        portion from its recorded charge; if it has been weighed, the bead
        comes from its recorded weighing too. Refusals come back in the
        problem list so they are reported alongside supersession problems
        rather than alone. An unknown crucible id is different in kind — like
        an unknown sample id, there is no interpretation to offer — so it
        raises :class:`CrucibleNotFoundError` directly.
        """
        if data.crucible_id is None:
            problems: list[str] = []
            if data.sample_weight_g is None:
                problems.append(
                    "sample_weight_g is required unless the result names the crucible "
                    "the sample was charged into"
                )
            if data.gold_bead_mg is None:
                problems.append(
                    "gold_bead_mg is required unless the result names a crucible "
                    "that has been weighed"
                )
            return None, data.sample_weight_g, data.gold_bead_mg, problems

        crucible = self._session.get(Crucible, data.crucible_id)
        if crucible is None:
            raise CrucibleNotFoundError(f"no crucible with id {data.crucible_id}")

        problems = []
        if data.sample_weight_g is not None:
            problems.append(
                "name the crucible or weigh the portion, not both: once a crucible is "
                "named, its recorded charge is the portion assayed"
            )
        if crucible.sample_id != sample.id:
            if crucible.sample_id is None:
                # A QC insertion: its bead belongs to the crucible row and is
                # judged by QC Sentinel on export — it is never entered as a
                # sample's result here.
                problems.append(
                    f"crucible #{crucible.id} holds a QC material (#{crucible.qc_material_id}), "
                    f"not sample #{sample.id}; results are entered for sample crucibles only"
                )
            else:
                problems.append(
                    f"crucible #{crucible.id} was charged with sample #{crucible.sample_id}, "
                    f"not #{sample.id}"
                )
        if crucible.status not in _BEAD_BEARING:
            problems.append(
                f"crucible #{crucible.id} is {crucible.status.value}; a bead exists only "
                "after cupellation"
            )

        bead_weight = data.gold_bead_mg
        weighed = crucible.status is CrucibleStatus.WEIGHED and crucible.gold_bead_mg is not None
        if weighed and data.gold_bead_mg is not None:
            problems.append(
                "the crucible has already been weighed; its recorded bead is what this "
                "assay produced, so gold_bead_mg must be left unset"
            )
        elif not weighed and data.gold_bead_mg is None:
            problems.append(
                "gold_bead_mg is required unless the result names a crucible that has been weighed"
            )

        portion_weight = None if problems else crucible.sample_weight_g
        if weighed and not problems:
            bead_weight = crucible.gold_bead_mg
        return crucible, portion_weight, bead_weight, problems
