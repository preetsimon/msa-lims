"""Quality-control material registration: the stock a batch's QC comes from.

Thin, like ``flux_recipes/service.py`` — one uniqueness check, one audit
event, and one rule that ties a material's fields to what the material *is*:
a CRM carries its certified grade; a blank is defined by having none. The
role gate is ``MAY_CONFIGURE_LAB``, same tier as flux recipes — deciding
what controls guard the lab's work is lab process configuration.

Only material-type QC lives here (CRM, blank, coarse blank). Duplicates are
insertions of an existing *sample*, not stock items, and are deliberately
not modelled yet (see PROGRESS.md). Nothing in this module judges results —
the LIMS records insertion and measurement; QC Sentinel judges on export.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, LabUser, QcMaterial
from msa_lims.domain.enums import MAY_CONFIGURE_LAB, QcMaterialType, Role
from msa_lims.domain.lifecycle import InsufficientRoleError

#: The types this table holds: physical materials a technician scoops from a
#: jar. The duplicate types in ``QcMaterialType`` name sample-based
#: insertions, which have no stock row to register.
_MATERIAL_TYPES: frozenset[QcMaterialType] = frozenset(
    {QcMaterialType.CRM, QcMaterialType.BLANK, QcMaterialType.COARSE_BLANK}
)


class QcMaterialNotFoundError(ValueError):
    """No QC material with this id exists.

    Lives here rather than in :mod:`msa_lims.batches.service`, for the same
    reason :class:`~msa_lims.clients.service.ClientNotFoundError` lives in
    ``clients/service.py`` — the module that owns an entity owns the error
    for "this one doesn't exist."
    """


class QcMaterialValidationError(ValueError):
    """One or more problems with a QC material registration, reported together."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))


@dataclass(frozen=True, slots=True)
class QcMaterialInput:
    name: str
    qc_type: QcMaterialType
    lot_number: str | None = None
    certified_au_value_g_t: Decimal | None = None
    certified_au_uncertainty_g_t: Decimal | None = None
    notes: str | None = None


class QcMaterialService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self, data: QcMaterialInput, *, registered_by: LabUser, actor_role: Role
    ) -> QcMaterial:
        if actor_role not in MAY_CONFIGURE_LAB:
            raise InsufficientRoleError(
                f"{actor_role.value} may not register a QC material; this needs one of "
                + ", ".join(sorted(role.value for role in MAY_CONFIGURE_LAB))
            )

        if data.qc_type not in _MATERIAL_TYPES:
            raise QcMaterialValidationError(
                [
                    f"{data.qc_type.value} is not a material; duplicates re-insert an "
                    "existing sample and cannot be registered as stock"
                ]
            )

        name = data.name.strip()
        problems: list[str] = []
        if not name:
            problems.append("material name cannot be blank")
        elif self._session.scalar(select(QcMaterial).where(QcMaterial.name == name)) is not None:
            problems.append(f"a QC material named {name!r} already exists")

        if data.qc_type is QcMaterialType.CRM:
            # A CRM without a certified value guards nothing — there would be
            # nothing to compare its result against.
            if data.certified_au_value_g_t is None:
                problems.append("a certified reference material requires a certified au value")
            if data.certified_au_uncertainty_g_t is None:
                problems.append("a certified reference material requires its certified uncertainty")
        elif (
            data.certified_au_value_g_t is not None or data.certified_au_uncertainty_g_t is not None
        ):
            problems.append(
                f"a {data.qc_type.value} has no certified grade; leave the certified "
                "value and uncertainty unset"
            )

        if problems:
            raise QcMaterialValidationError(problems)

        material = QcMaterial(
            name=name,
            qc_type=data.qc_type,
            lot_number=data.lot_number,
            certified_au_value_g_t=data.certified_au_value_g_t,
            certified_au_uncertainty_g_t=data.certified_au_uncertainty_g_t,
            notes=data.notes,
        )
        self._session.add(material)
        self._session.flush()

        self._session.add(
            AuditEvent(
                table_name="qc_material",
                record_id=material.id,
                action="create",
                after={
                    "name": material.name,
                    "qc_type": material.qc_type.value,
                    "certified_au_value_g_t": (
                        None
                        if material.certified_au_value_g_t is None
                        else str(material.certified_au_value_g_t)
                    ),
                },
                actor_id=registered_by.id,
            )
        )
        return material
