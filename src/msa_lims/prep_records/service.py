"""Prep record entry — the audit trail for what physically happened during
sample preparation.

Unlike fire assay results, prep records are not append-only by database
grant (yet).  They follow the same logical pattern — corrections are new
rows with ``supersedes_id`` — but the UPDATE/DELETE grants are not yet
revoked.  This is a deliberate simplification: prep records are lower-risk
than analytical results, and the full append-only enforcement can be added
later when the prep workflow is more settled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from msa_lims.db.audit import record_audit_event
from msa_lims.db.models import Instrument, LabUser, PrepRecord, Sample
from msa_lims.domain.enums import PrepStage, Role
from msa_lims.domain.lifecycle import BENCH_ROLES, InsufficientRoleError
from msa_lims.fire_assay_results.service import SampleNotFoundError


class PrepRecordValidationError(ValueError):
    """One or more problems with a prep record entry, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class PrepRecordInput:
    sample_id: int
    stage: PrepStage
    instrument_id: int | None = None
    performed_at: datetime | None = None
    input_weight_g: Decimal | None = None
    output_weight_g: Decimal | None = None
    supersedes_id: int | None = None
    superseded_reason: str | None = None
    notes: str | None = None


class PrepRecordService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, data: PrepRecordInput, *, actor: LabUser, actor_role: Role) -> PrepRecord:
        if actor_role not in BENCH_ROLES:
            raise InsufficientRoleError(
                f"{actor_role.value} may not enter a prep record; this needs one of "
                + ", ".join(sorted(role.value for role in BENCH_ROLES))
            )

        sample = self._session.get(Sample, data.sample_id)
        if sample is None:
            raise SampleNotFoundError(f"no sample with id {data.sample_id}")

        problems: list[str] = []

        if data.instrument_id is not None:
            instrument = self._session.get(Instrument, data.instrument_id)
            if instrument is None:
                problems.append(f"no instrument with id {data.instrument_id}")

        if data.supersedes_id is not None:
            existing = self._session.get(PrepRecord, data.supersedes_id)
            if existing is None:
                problems.append(f"no prep record with id {data.supersedes_id}")
            elif existing.sample_id != data.sample_id:
                problems.append(
                    f"superseded record {data.supersedes_id} belongs to sample "
                    f"{existing.sample_id}, not {data.sample_id}"
                )
            if not data.superseded_reason or not data.superseded_reason.strip():
                problems.append("superseded_reason is required when superseding a record")

        if (
            data.input_weight_g is not None
            and data.output_weight_g is not None
            and data.output_weight_g > data.input_weight_g
        ):
            problems.append("output weight cannot exceed input weight")

        if problems:
            raise PrepRecordValidationError(problems)

        record = PrepRecord(
            sample_id=data.sample_id,
            stage=data.stage,
            instrument_id=data.instrument_id,
            prep_tech_id=actor.id,
            performed_at=data.performed_at or datetime.now().astimezone(),
            input_weight_g=data.input_weight_g,
            output_weight_g=data.output_weight_g,
            supersedes_id=data.supersedes_id,
            superseded_reason=data.superseded_reason,
            notes=data.notes,
        )
        self._session.add(record)
        self._session.flush()

        after: dict[str, object] = {
            "sample_id": data.sample_id,
            "stage": data.stage.value,
        }
        if data.instrument_id is not None:
            after["instrument_id"] = data.instrument_id

        record_audit_event(
            self._session,
            table_name="prep_record",
            record_id=record.id,
            action="amend" if data.supersedes_id else "create",
            actor_id=actor.id,
            after=after,
            reason=data.superseded_reason,
        )
        return record
