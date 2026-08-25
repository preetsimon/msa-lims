"""Sample lifecycle moves that carry no data beyond the status itself.

Every other status change in this codebase has been reached honestly since
Phase 0 named the gap: fire assay result entry moves a sample to ``ASSAYED``,
certificate issuance moves it to ``REPORTED``, and (as of this phase) charging
a crucible moves it to ``IN_ASSAY`` — each because that write path's own data
(a bead weight, a signed PDF, a furnace slot) is what makes the move true.
``RECEIVED -> IN_PREP``, ``IN_PREP -> READY_FOR_ASSAY``, the pulp shortcut,
returning a sample for re-assay, and rejecting a sample carry no such
data — a bare status flip *is* the whole fact, with an optional reason where
:mod:`msa_lims.domain.lifecycle` requires one. This module is where those
moves finally go through :func:`~msa_lims.domain.lifecycle.check_transition`
for real, closing the gap every earlier phase's docstring named and deferred.

**This is not a general "set any status" endpoint.** ``IN_ASSAY``, ``ASSAYED``
and ``REPORTED`` are deliberately excluded from the request shape (see
``SampleStatusUpdate`` in ``web/schemas.py``) — each of those is only ever
true because of the record that produced it (a crucible, a result, a
certificate), and a bare status flip here would create one with nothing
behind it, exactly the "a status advance with nothing behind it would be a
claim about the world nobody made" problem ``batches/service.py`` already
states for crucible transitions.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, LabUser, Sample
from msa_lims.domain.enums import Role, SampleStatus
from msa_lims.domain.lifecycle import check_transition
from msa_lims.fire_assay_results.service import SampleNotFoundError


class SampleLifecycleService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def advance(
        self,
        sample_id: int,
        *,
        target: SampleStatus,
        reason: str | None,
        actor: LabUser,
        actor_role: Role,
    ) -> Sample:
        sample = self._session.get(Sample, sample_id)
        if sample is None:
            raise SampleNotFoundError(f"no sample with id {sample_id}")

        before = sample.status
        # Raises TransitionNotAllowedError, InsufficientRoleError, or
        # ReasonRequiredError; all three propagate and are mapped globally in
        # web/app.py, same as every other lifecycle refusal in this codebase.
        check_transition(
            source=before,
            target=target,
            sample_type=sample.sample_type,
            role=actor_role,
            reason=reason,
        )

        sample.status = target
        self._session.add(
            AuditEvent(
                table_name="sample",
                record_id=sample.id,
                action="transition",
                before={"status": before.value},
                after={"status": target.value},
                reason=reason,
                actor_id=actor.id,
            )
        )
        return sample
