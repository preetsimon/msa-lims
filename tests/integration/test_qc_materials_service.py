"""QC material registration, against a real Postgres session.

The service is thin — one role gate, one type rule, one uniqueness check, one
audit event — so the tests here prove each of those four things directly,
the way ``test_flux_recipes_service.py`` does for its own registration
service.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.db.models import AuditEvent, LabUser, QcMaterial
from msa_lims.domain.enums import QcMaterialType, Role
from msa_lims.domain.lifecycle import InsufficientRoleError
from msa_lims.qc_materials.service import (
    QcMaterialInput,
    QcMaterialService,
    QcMaterialValidationError,
    list_qc_materials,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def supervisor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-supervisor-qcmat",
        email="sup-qcmat@lab.test",
        full_name="S. Upervisor",
        role=Role.SUPERVISOR,
    )
    app_session.add(user)
    app_session.flush()
    return user


def crm_input(**overrides: object) -> QcMaterialInput:
    defaults: dict[str, object] = {
        "name": "OREAS 501d",
        "qc_type": QcMaterialType.CRM,
        "lot_number": "LOT-2026-A",
        "certified_au_value_g_t": Decimal("1.54"),
        "certified_au_uncertainty_g_t": Decimal("0.06"),
        "notes": None,
    }
    defaults.update(overrides)
    return QcMaterialInput(**defaults)  # type: ignore[arg-type]


class TestRegisteringAMaterial:
    def test_a_crm_is_registered_with_its_certified_grade(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        material = QcMaterialService(app_session).create(
            crm_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR
        )
        app_session.flush()

        assert material.id is not None
        assert material.qc_type is QcMaterialType.CRM
        assert material.certified_au_value_g_t == Decimal("1.54")
        assert material.is_active is True

    def test_registration_writes_a_create_audit_event(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        material = QcMaterialService(app_session).create(
            crm_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR
        )
        app_session.flush()

        event = app_session.scalar(
            select(AuditEvent).where(
                AuditEvent.table_name == "qc_material", AuditEvent.record_id == material.id
            )
        )
        assert event is not None
        assert event.action == "create"
        assert event.after["qc_type"] == "crm"
        assert event.after["certified_au_value_g_t"] == "1.54"

    def test_a_duplicate_name_is_refused_naming_the_conflict(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        service = QcMaterialService(app_session)
        service.create(crm_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR)
        app_session.flush()

        with pytest.raises(QcMaterialValidationError, match="already exists"):
            service.create(crm_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR)

    def test_an_analyst_may_not_register_a_material(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        with pytest.raises(InsufficientRoleError):
            QcMaterialService(app_session).create(
                crm_input(), registered_by=supervisor, actor_role=Role.ANALYST
            )


class TestTheTypeRule:
    """A CRM carries its certified grade; a blank is defined by having none."""

    def test_a_crm_without_a_certified_value_is_refused(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        with pytest.raises(QcMaterialValidationError, match="certified au value"):
            QcMaterialService(app_session).create(
                crm_input(certified_au_value_g_t=None, certified_au_uncertainty_g_t=None),
                registered_by=supervisor,
                actor_role=Role.SUPERVISOR,
            )

    def test_a_blank_with_a_certified_value_is_refused(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        with pytest.raises(QcMaterialValidationError, match="no certified grade"):
            QcMaterialService(app_session).create(
                crm_input(qc_type=QcMaterialType.BLANK),
                registered_by=supervisor,
                actor_role=Role.SUPERVISOR,
            )

    def test_a_blank_without_one_registers_fine(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        material = QcMaterialService(app_session).create(
            crm_input(
                name="Silica Sand Blank",
                qc_type=QcMaterialType.BLANK,
                lot_number=None,
                certified_au_value_g_t=None,
                certified_au_uncertainty_g_t=None,
            ),
            registered_by=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()
        assert material.certified_au_value_g_t is None

    def test_both_crm_problems_are_reported_together(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        """A CRM missing value *and* uncertainty names both, not the first."""
        with pytest.raises(QcMaterialValidationError) as excinfo:
            QcMaterialService(app_session).create(
                crm_input(certified_au_value_g_t=None, certified_au_uncertainty_g_t=None),
                registered_by=supervisor,
                actor_role=Role.SUPERVISOR,
            )
        messages = " ".join(excinfo.value.problems)
        assert "certified au value" in messages
        assert "uncertainty" in messages

    def test_a_duplicate_type_has_no_stock_row(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        """Duplicates re-insert an existing sample; they are not materials."""
        with pytest.raises(QcMaterialValidationError, match="not a material"):
            QcMaterialService(app_session).create(
                crm_input(qc_type=QcMaterialType.FIELD_DUPLICATE),
                registered_by=supervisor,
                actor_role=Role.SUPERVISOR,
            )


class TestStockRowsAreMutable:
    def test_the_application_role_can_retire_a_lot_in_place(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        """``qc_material`` is mutable-tier: retirement is an UPDATE to
        ``is_active``, which the restricted application role must be allowed
        to make — historical batches still name the row."""
        material = QcMaterialService(app_session).create(
            crm_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR
        )
        app_session.flush()

        material.is_active = False
        app_session.flush()
        app_session.refresh(material)
        assert material.is_active is False

    def test_a_stored_negative_certified_value_is_refused_by_the_database(
        self, app_session: Session
    ) -> None:
        """The CHECK constraints are the schema's own backstop behind the
        service's validation."""
        from sqlalchemy.exc import IntegrityError

        app_session.add(
            QcMaterial(
                name="Bad Lot",
                qc_type=QcMaterialType.CRM,
                certified_au_value_g_t=Decimal("-1"),
                certified_au_uncertainty_g_t=Decimal("0.05"),
            )
        )
        with pytest.raises(IntegrityError):
            app_session.flush()


class TestListingMaterials:
    def test_materials_come_back_by_name(self, app_session: Session, supervisor: LabUser) -> None:
        service = QcMaterialService(app_session)
        service.create(
            crm_input(name="OREAS 501d"), registered_by=supervisor, actor_role=Role.SUPERVISOR
        )
        service.create(
            crm_input(
                name="Silica Blank",
                qc_type=QcMaterialType.BLANK,
                lot_number=None,
                certified_au_value_g_t=None,
                certified_au_uncertainty_g_t=None,
            ),
            registered_by=supervisor,
            actor_role=Role.SUPERVISOR,
        )
        app_session.flush()

        names = [material.name for material in list_qc_materials(app_session)]
        assert names == ["OREAS 501d", "Silica Blank"]

    def test_an_empty_lab_lists_nothing(self, app_session: Session) -> None:
        assert list_qc_materials(app_session) == []

    def test_a_retired_material_is_excluded_by_default(
        self, app_session: Session, supervisor: LabUser
    ) -> None:
        material = QcMaterialService(app_session).create(
            crm_input(), registered_by=supervisor, actor_role=Role.SUPERVISOR
        )
        material.is_active = False
        app_session.flush()

        assert list_qc_materials(app_session) == []
        assert list_qc_materials(app_session, active_only=False) == [material]
