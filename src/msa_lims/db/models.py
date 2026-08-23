"""The persistent model.

Scope note: this is the **spine** — the entities a sample needs to exist and be
found. Preparation, batching, results and certificates arrive in later phases
and are deliberately absent rather than stubbed, so that no table here is shaped
by a guess about a workflow that has not been built.

Three conventions run through everything below and are worth reading once:

* **Enums are stored as their string values**, via ``native_enum=False`` and a
  VARCHAR with a CHECK. Postgres native enums cannot have a value removed
  without a table rewrite, and a laboratory vocabulary does change.
* **Nothing is soft-deleted.** Rows that stop being relevant are marked inactive
  and stay legible, because a certificate issued in 2024 refers to a client and
  a method as they were in 2024.
* **Weights and grades are NUMERIC without precision.** See ``base.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from msa_lims.db.base import Base, IdPk, TimestampMixin
from msa_lims.domain.enums import (
    InstrumentStatus,
    InstrumentType,
    Role,
    SampleStatus,
    SampleType,
)


def _enum(enum_type: type, name: str) -> SaEnum:
    """A VARCHAR column constrained to an enum's values.

    ``native_enum=False`` keeps the vocabulary in a CHECK constraint rather than
    a Postgres type: adding a value is then an ordinary constraint change and
    removing one does not require rewriting the table.
    """
    return SaEnum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda enum: [member.value for member in enum],
        length=32,
    )


class LabUser(Base, TimestampMixin):
    """Someone who acts in the system.

    Identity comes from the OIDC provider; this row exists so that a result can
    reference the analyst who produced it with a foreign key rather than a
    free-text name that changes when somebody marries.
    """

    __tablename__ = "lab_user"

    id: Mapped[IdPk]
    subject: Mapped[str] = mapped_column(String(255), unique=True)
    """The provider's stable identifier for this person — never the email,
    which people change."""
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[Role] = mapped_column(_enum(Role, "role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Client(Base, TimestampMixin):
    """A mining company that submits samples."""

    __tablename__ = "client"

    id: Mapped[IdPk]
    code: Mapped[str] = mapped_column(String(12), unique=True)
    """Short code used in sample labels and on certificates, e.g. 'MSA'."""
    name: Mapped[str] = mapped_column(String(200), unique=True)
    contact_person: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    billing_address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    projects: Mapped[list[Project]] = relationship(back_populates="client")


class Project(Base, TimestampMixin):
    """A drilling program or sampling campaign."""

    __tablename__ = "project"
    __table_args__ = (
        UniqueConstraint("client_id", "name", name="client_name"),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="dates_ordered",
        ),
    )

    id: Mapped[IdPk]
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    """Free text, e.g. 'Red Lake, ON'. Deliberately not a coordinate: this is
    what a person writes on a report, and the precise geometry lives on the
    holes and the samples."""
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    client: Mapped[Client] = relationship(back_populates="projects")
    drill_holes: Mapped[list[DrillHole]] = relationship(back_populates="project")


class DrillHole(Base, TimestampMixin):
    """Geological context for drill samples.

    Collar coordinates are UTM. The zone is stored explicitly because a northing
    without a zone is ambiguous across the country, and a project that spans two
    zones is not unusual.
    """

    __tablename__ = "drill_hole"
    __table_args__ = (
        UniqueConstraint("project_id", "hole_id", name="project_hole"),
        CheckConstraint("total_depth_m IS NULL OR total_depth_m > 0", name="depth_positive"),
        CheckConstraint("dip_degrees IS NULL OR dip_degrees BETWEEN -90 AND 90", name="dip_range"),
        CheckConstraint(
            "azimuth_degrees IS NULL OR azimuth_degrees >= 0 AND azimuth_degrees < 360",
            name="azimuth_range",
        ),
    )

    id: Mapped[IdPk]
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), index=True)
    hole_id: Mapped[str] = mapped_column(String(50))
    """As geologists write it, e.g. 'MSA-24-001'."""

    easting: Mapped[Decimal | None] = mapped_column(Numeric)
    northing: Mapped[Decimal | None] = mapped_column(Numeric)
    elevation_m: Mapped[Decimal | None] = mapped_column(Numeric)
    utm_zone: Mapped[str | None] = mapped_column(String(8))

    total_depth_m: Mapped[Decimal | None] = mapped_column(Numeric)
    dip_degrees: Mapped[Decimal | None] = mapped_column(Numeric)
    """Negative is downward, the geological convention: a vertical hole is -90."""
    azimuth_degrees: Mapped[Decimal | None] = mapped_column(Numeric)
    drilling_method: Mapped[str | None] = mapped_column(String(20))

    project: Mapped[Project] = relationship(back_populates="drill_holes")
    samples: Mapped[list[Sample]] = relationship(back_populates="drill_hole")


class Submission(Base, TimestampMixin):
    """A work order: the batch of material a client delivered on one day.

    This is the chain-of-custody entry document, and it is the thing a
    certificate reports against. ``declared_sample_count`` is what the client's
    paperwork said; the actual number of sample rows is what arrived. They
    disagree often enough that the discrepancy is recorded rather than
    reconciled away — a missing bag is a conversation with the client, not a
    data-entry correction.
    """

    __tablename__ = "submission"
    __table_args__ = (
        UniqueConstraint("submission_number", name="number"),
        CheckConstraint(
            "declared_sample_count IS NULL OR declared_sample_count >= 0",
            name="declared_count_non_negative",
        ),
    )

    id: Mapped[IdPk]
    submission_number: Mapped[str] = mapped_column(String(30))
    """Lab-assigned, e.g. 'SUB-2026-0841'."""
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"), index=True)

    client_reference: Mapped[str | None] = mapped_column(String(100))
    purchase_order: Mapped[str | None] = mapped_column(String(100))

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_by_id: Mapped[int | None] = mapped_column(ForeignKey("lab_user.id"))
    declared_sample_count: Mapped[int | None] = mapped_column(Integer)

    rush: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    requested_tat_days: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[str | None] = mapped_column(Text)

    samples: Mapped[list[Sample]] = relationship(back_populates="submission")


class Sample(Base, TimestampMixin):
    """The core entity: one bag of material with an identity.

    ``sample_id`` is unique across the whole lab, not per submission. Two
    clients can and do use the same internal numbering, but the barcode on the
    bag has to mean one thing at the crusher — so the label carries the client's
    property code and the uniqueness is global.

    Depth columns are populated from the parsed label for drill samples and are
    NULL for surface samples. The CHECK enforces that they arrive together and
    in order; overlap between samples in one hole is a submission-level check in
    :mod:`msa_lims.domain.sample_id`, since it cannot be expressed on one row.
    """

    __tablename__ = "sample"
    __table_args__ = (
        UniqueConstraint("sample_id", name="label"),
        CheckConstraint(
            "(from_depth_m IS NULL) = (to_depth_m IS NULL)",
            name="depths_together",
        ),
        CheckConstraint(
            "from_depth_m IS NULL OR (from_depth_m >= 0 AND to_depth_m > from_depth_m)",
            name="interval_advances",
        ),
        # A drill sample without a hole is a sample nobody can place on a
        # section. The two must arrive together.
        CheckConstraint(
            "drill_hole_id IS NOT NULL OR from_depth_m IS NULL",
            name="interval_requires_hole",
        ),
        CheckConstraint(
            "weight_received_g IS NULL OR weight_received_g > 0",
            name="received_weight_positive",
        ),
        Index("ix_sample_hole_interval", "drill_hole_id", "from_depth_m"),
    )

    id: Mapped[IdPk]
    sample_id: Mapped[str] = mapped_column(String(100))
    """The barcode label, e.g. 'MSA-24-001-142.50_144.00'."""

    submission_id: Mapped[int] = mapped_column(ForeignKey("submission.id"), index=True)
    drill_hole_id: Mapped[int | None] = mapped_column(ForeignKey("drill_hole.id"), index=True)

    sample_type: Mapped[SampleType] = mapped_column(_enum(SampleType, "sample_type"))
    status: Mapped[SampleStatus] = mapped_column(_enum(SampleStatus, "sample_status"), index=True)

    from_depth_m: Mapped[Decimal | None] = mapped_column(Numeric)
    to_depth_m: Mapped[Decimal | None] = mapped_column(Numeric)

    # Surface samples carry their own location; drill samples inherit the hole's.
    easting: Mapped[Decimal | None] = mapped_column(Numeric)
    northing: Mapped[Decimal | None] = mapped_column(Numeric)
    elevation_m: Mapped[Decimal | None] = mapped_column(Numeric)

    lithology_code: Mapped[str | None] = mapped_column(String(20))
    alteration_code: Mapped[str | None] = mapped_column(String(20))

    weight_received_g: Mapped[Decimal | None] = mapped_column(Numeric)

    comments: Mapped[str | None] = mapped_column(Text)

    submission: Mapped[Submission] = relationship(back_populates="samples")
    drill_hole: Mapped[DrillHole | None] = relationship(back_populates="samples")


class Instrument(Base, TimestampMixin):
    """A machine whose identity belongs on a result.

    Crushers and pulverizers are here alongside the spectrometers because a
    contamination investigation traces material back through the equipment that
    touched it, and 'which pulverizer' is the question that gets asked.
    """

    __tablename__ = "instrument"

    id: Mapped[IdPk]
    name: Mapped[str] = mapped_column(String(50), unique=True)
    instrument_type: Mapped[InstrumentType] = mapped_column(
        _enum(InstrumentType, "instrument_type")
    )
    manufacturer: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    serial_number: Mapped[str | None] = mapped_column(String(100))
    location: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[InstrumentStatus] = mapped_column(
        _enum(InstrumentStatus, "instrument_status"),
        default=InstrumentStatus.ACTIVE,
    )
    calibration_due_on: Mapped[date | None] = mapped_column(Date)
    """A date, not a timestamp: calibration certificates expire on a day."""


class AuditEvent(Base, TimestampMixin):
    """Every change to a row that matters, kept forever.

    This table is **append-only, enforced by database grants** rather than by
    convention — the application role holds no UPDATE or DELETE on it (see the
    ``append_only_grants`` migration). An audit table the application can edit
    records what the application currently believes, which is not an audit.

    ``before`` and ``after`` are JSONB snapshots of the changed columns only, not
    the whole row. A full-row snapshot doubles the storage and still leaves the
    reader diffing two blobs to find what moved; the columns that changed are
    the question anyone is actually asking.

    ``reason`` is required for amendments. A corrected result without a stated
    reason is the single most common finding in a laboratory audit, so the
    column is NOT NULL for the actions where it applies rather than a hopeful
    convention in the service layer.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        CheckConstraint(
            "action <> 'amend' OR (reason IS NOT NULL AND length(trim(reason)) > 0)",
            name="amendment_states_reason",
        ),
        Index("ix_audit_event_target", "table_name", "record_id"),
    )

    id: Mapped[IdPk]
    table_name: Mapped[str] = mapped_column(String(63))
    record_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(20))
    """One of create, amend, supersede, transition. Not an enum type: the audit
    log must be able to record an action from a future version of the schema
    without a migration, because losing the event is worse than an unfamiliar
    verb."""

    before: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)

    actor_id: Mapped[int | None] = mapped_column(ForeignKey("lab_user.id"), index=True)
    """Nullable only so that a system action — a scheduled job — can be recorded
    honestly as having no human actor, rather than being attributed to whoever
    happened to trigger it."""
    actor_ip: Mapped[str | None] = mapped_column(String(45))
    """45 characters holds an IPv6 address with an IPv4 tail."""
