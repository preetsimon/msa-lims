"""The persistent model.

Scope note: this is the **spine** — the entities a sample needs to exist, be
found, be assayed, and (as of Phase 1's Certificate of Analysis) be reported
— plus, as of Phase 2, the furnace batch a sample is charged into on its way
to a result. Prep stage tracking is still absent rather than stubbed, so that
no table here is shaped by a guess about a workflow that has not been built.

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
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SaEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from msa_lims.db.base import Base, IdPk, Sha256, TimestampMixin
from msa_lims.domain.enums import (
    AssayMethod,
    BatchStatus,
    CrucibleStatus,
    InstrumentStatus,
    InstrumentType,
    MatrixType,
    QcMaterialType,
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

    ``prev_entry_hash``/``entry_hash`` chain every row to the one before it
    (audit idea #1, "The Ledger That Signs Itself") — see
    :mod:`msa_lims.domain.audit_chain`. Append-only-by-grant stops this
    application from editing history; the chain makes an edit from *outside*
    the application (a direct `UPDATE` as a more privileged role, a restored
    backup with one row altered) detectable by recomputing hashes, not just
    forbidden. Written only through :func:`msa_lims.db.audit.record_audit_event`
    — never construct this class directly outside that function, or the row
    will carry no hash and silently break the chain for everything after it.
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

    prev_entry_hash: Mapped[Sha256 | None]
    """`NULL` for exactly one row: the genesis entry, the first one this
    table ever held. Every other row's value is the `entry_hash` of the row
    immediately before it by `id`."""
    entry_hash: Mapped[Sha256]
    """`sha256(prev_entry_hash ∥ canonical(this entry))`, hex-encoded — see
    `domain/audit_chain.py`. Unique by construction (it commits to `id`'s own
    predecessor chain, which is itself unique), but not declared `UNIQUE`:
    that would let a stranger check "does this hash exist" without a
    predecessor to compare against, which is a smaller claim than the real
    one — that recomputing the *whole* chain reproduces this exact value."""


class FireAssayResult(Base, TimestampMixin):
    """One gravimetric fire assay result.

    **Append-only, enforced by database grants** — same mechanism as
    ``audit_event``. A corrected result is a new row whose ``supersedes_id``
    points at the row it corrects, with a required ``superseded_reason``,
    never an ``UPDATE`` to the original. Only the row nothing else supersedes
    is *current* for a sample; the service layer refuses to supersede a row
    that has already been superseded, so a chain cannot branch (mirrors QC
    Sentinel's rule against double-replacement in a re-assay chain).

    The raw measurements are stored alongside the computed grade, not only the
    grade — the columns below are what
    :mod:`msa_lims.domain.assay` was actually called with, so the number on a
    certificate is reproducible from the bench record years later, not just
    asserted.

    **One table, two finishes, one supersession chain.** A gravimetric result
    weighs the parted bead (``gold_bead_mg``); an AAS or ICP-MS result reads
    the dissolved bead's concentration (``solution_*``). Exactly one set is
    populated, matched to ``method`` and enforced by CHECK constraints, which
    is why ``gold_bead_mg`` is nullable despite being mandatory for the
    gravimetric path.

    They share this table rather than getting one each because they are the
    same fusion finished two ways, and because the workflow that matters most
    runs *between* them: a solution finish that saturates is re-run
    gravimetrically, and that referee result supersedes the first. Split
    across two tables, "the current result for this sample" would stop being
    a single question with a single answer — and a certificate could freeze
    one table's head while the other held a newer correction.

    ``crucible_id`` is nullable on purpose: direct entry remains a real path
    (externally assayed pulp arrives with no crucible this system charged),
    and a result naming one has its portion weight derived from the crucible's
    recorded charge rather than re-typed — see the service module docstring.

    ``analysed_at`` is instrument/bench wall-clock time, distinct from
    ``created_at`` — see the ``TimestampMixin`` docstring in ``db/base.py``
    for why the two are never conflated.
    """

    __tablename__ = "fire_assay_result"
    __table_args__ = (
        CheckConstraint(
            "supersedes_id IS NULL OR "
            "(superseded_reason IS NOT NULL AND length(trim(superseded_reason)) > 0)",
            name="supersession_states_reason",
        ),
        CheckConstraint("sample_weight_g > 0", name="sample_weight_positive"),
        CheckConstraint("gold_bead_mg >= 0", name="bead_weight_non_negative"),
        CheckConstraint("solution_volume_ml > 0", name="solution_volume_positive"),
        CheckConstraint("solution_concentration >= 0", name="solution_concentration_non_negative"),
        # The finish's inputs must match the finish that was performed. Stated
        # as two constraints rather than one so a violation names which half is
        # wrong: a gravimetric row carrying a concentration and one missing its
        # bead are different mistakes.
        CheckConstraint(
            "method <> 'fire_assay_gravimetric' OR "
            "(gold_bead_mg IS NOT NULL AND solution_concentration IS NULL "
            "AND solution_volume_ml IS NULL AND solution_concentration_unit IS NULL)",
            name="gravimetric_weighs_a_bead",
        ),
        CheckConstraint(
            "method = 'fire_assay_gravimetric' OR "
            "(solution_concentration IS NOT NULL AND solution_volume_ml IS NOT NULL "
            "AND solution_concentration_unit IS NOT NULL AND gold_bead_mg IS NULL)",
            name="solution_finish_reads_a_concentration",
        ),
    )

    id: Mapped[IdPk]
    sample_id: Mapped[int] = mapped_column(ForeignKey("sample.id"), index=True)
    method: Mapped[AssayMethod] = mapped_column(_enum(AssayMethod, "assay_method"))

    #: The gravimetric finish's measurement. Null on an AAS/ICP-MS row — see
    #: the class docstring and the CHECK constraints above.
    gold_bead_mg: Mapped[Decimal | None] = mapped_column(Numeric)
    sample_weight_g: Mapped[Decimal] = mapped_column(Numeric)
    """The portion assayed. Common to both finishes: every route through the
    furnace starts by weighing out material, and it is the divisor in both
    grade calculations."""
    balance_sensitivity_mg: Mapped[Decimal | None] = mapped_column(Numeric)

    # The solution finish's measurements — the dissolved bead's concentration,
    # the volume it was made up to, and the method's detection limit in the
    # same unit as the reading. Null on a gravimetric row.
    #
    # The method's *upper* calibration limit is deliberately not stored: it
    # gates whether a reading may be recorded at all (see
    # ``domain.assay.solution_finish_grade``) but contributes nothing to the
    # number that ends up here, and it describes the method rather than this
    # result. It belongs on an instrument/method record when one exists.
    solution_concentration: Mapped[Decimal | None] = mapped_column(Numeric)
    solution_concentration_unit: Mapped[str | None] = mapped_column(String(16))
    solution_volume_ml: Mapped[Decimal | None] = mapped_column(Numeric)
    solution_detection_limit: Mapped[Decimal | None] = mapped_column(Numeric)

    # The computed grade, stored across columns rather than one JSON blob so
    # it stays queryable and constrainable — the same shape
    # domain.values.MeasuredValue enforces in memory, carried into the schema.
    au_value: Mapped[Decimal | None] = mapped_column(Numeric)
    au_detection_limit: Mapped[Decimal | None] = mapped_column(Numeric)
    au_censored: Mapped[bool] = mapped_column(Boolean)
    au_unit: Mapped[str] = mapped_column(String(16))

    analyst_id: Mapped[int] = mapped_column(ForeignKey("lab_user.id"))
    analysed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: The crucible this assay came from, when the sample was charged into a
    #: batch. Null for direct entry. When set, ``sample_weight_g`` is copied
    #: from that crucible's recorded charge at write time — see
    #: ``fire_assay_results/service.py``.
    crucible_id: Mapped[int | None] = mapped_column(
        ForeignKey("crucible.id"), index=True, nullable=True
    )

    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("fire_assay_result.id"))
    superseded_reason: Mapped[str | None] = mapped_column(Text)

    notes: Mapped[str | None] = mapped_column(Text)


class Certificate(Base, TimestampMixin):
    """A signed Certificate of Analysis.

    **Append-only**, same mechanism as ``audit_event`` and
    ``fire_assay_result``. An amendment is a new row whose ``supersedes_id``
    points at the certificate it corrects, with a required
    ``superseded_reason`` — never an ``UPDATE`` to the original or its stored
    PDF.

    Unlike ``fire_assay_result``, there is **no "only one current
    certificate" rule**: a client can hold many independent certificates over
    time, one per batch of samples reported. Supersession only prevents a
    single chain from branching — the certificate a specific correction
    replaces must itself not already be superseded — it does not limit how
    many separate certificates a client may have.

    The PDF is stored inline (``pdf_bytes``) rather than in a dedicated
    content-addressed blob store — a Phase 1 simplification (see
    PROGRESS.md), not a design commitment; QC Sentinel's write-once,
    hash-verified ``storage/blob.py`` is the natural next step if raw
    exports or attachments ever need the same treatment here.
    ``pdf_sha256`` is a real content hash of ``pdf_bytes``, re-verified on
    every read (see ``web/routes/certificates.py``), so a row that has
    somehow drifted from what it claims to contain is refused rather than
    served.
    """

    __tablename__ = "certificate"
    __table_args__ = (
        CheckConstraint(
            "supersedes_id IS NULL OR "
            "(superseded_reason IS NOT NULL AND length(trim(superseded_reason)) > 0)",
            name="supersession_states_reason",
        ),
    )

    id: Mapped[IdPk]
    certificate_number: Mapped[str] = mapped_column(String(30), unique=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), index=True)
    issued_by_id: Mapped[int] = mapped_column(ForeignKey("lab_user.id"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("certificate.id"))
    superseded_reason: Mapped[str | None] = mapped_column(Text)

    pdf_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    pdf_sha256: Mapped[Sha256]

    notes: Mapped[str | None] = mapped_column(Text)


class CertificateResult(Base, TimestampMixin):
    """One sample's result, frozen into a certificate at the moment it was issued.

    Points at the **specific** ``fire_assay_result`` row certified, not just
    the sample. If that result is later superseded, this row still records
    what the certificate actually reported — which is the entire reason a
    certificate exists as a document rather than a live query: it is a
    historical statement, not a view.
    """

    __tablename__ = "certificate_result"
    __table_args__ = (UniqueConstraint("certificate_id", "sample_id", name="certificate_sample"),)

    id: Mapped[IdPk]
    certificate_id: Mapped[int] = mapped_column(ForeignKey("certificate.id"), index=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("sample.id"), index=True)
    fire_assay_result_id: Mapped[int] = mapped_column(ForeignKey("fire_assay_result.id"))


class FluxRecipe(Base, TimestampMixin):
    """A named flux formula, calibrated to a nominal sample portion.

    Mutable, unlike ``fire_assay_result`` and ``certificate`` — a recipe is
    lab reference data, amended in place as reagent sourcing or a
    metallurgist's formulation changes, the same way ``instrument`` rows are.
    What must never change after the fact is a crucible's own frozen charge
    (see ``Crucible``); this row is only ever the current formula, not a
    history of every version a batch was charged under.

    Reagent columns allow zero — not every recipe uses every reagent — but
    never a negative amount.
    """

    __tablename__ = "flux_recipe"
    __table_args__ = (
        CheckConstraint("nominal_portion_g > 0", name="nominal_portion_positive"),
        CheckConstraint("litharge_g >= 0", name="litharge_non_negative"),
        CheckConstraint("soda_ash_g >= 0", name="soda_ash_non_negative"),
        CheckConstraint("borax_g >= 0", name="borax_non_negative"),
        CheckConstraint("silica_g >= 0", name="silica_non_negative"),
        CheckConstraint("flour_g >= 0", name="flour_non_negative"),
        CheckConstraint("nitre_g >= 0", name="nitre_non_negative"),
    )

    id: Mapped[IdPk]
    name: Mapped[str] = mapped_column(String(100), unique=True)
    matrix_type: Mapped[MatrixType] = mapped_column(_enum(MatrixType, "matrix_type"))
    nominal_portion_g: Mapped[Decimal] = mapped_column(Numeric)

    litharge_g: Mapped[Decimal] = mapped_column(Numeric)
    soda_ash_g: Mapped[Decimal] = mapped_column(Numeric)
    borax_g: Mapped[Decimal] = mapped_column(Numeric)
    silica_g: Mapped[Decimal] = mapped_column(Numeric)
    flour_g: Mapped[Decimal] = mapped_column(Numeric)
    nitre_g: Mapped[Decimal] = mapped_column(Numeric)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class QcMaterial(Base, TimestampMixin):
    """A quality-control material the lab keeps in stock and inserts into batches.

    A CRM carries a certified gold grade (with its uncertainty) from the
    certifying body's certificate; a blank is defined by having none — its
    whole point is that nothing should come back. Both are recorded here as
    stock reference data, mutable like ``flux_recipe``/``instrument``: a lot
    is retired by ``is_active``, not deletion, because historical batches
    still name it.

    The certified grade's unit is deliberately fixed to ``g/t`` rather than
    carried as a column: fire assay grades are ``g/t`` everywhere in this
    system (see ``domain/units.py``), and a second unit vocabulary for one
    column would be two conventions where one exists.

    Duplicate insertions (``FIELD_DUPLICATE`` etc. in ``QcMaterialType``) are
    *not* materials and have no row here — a duplicate re-inserts an existing
    sample, so it names a sample, not a stock item. That path is deferred
    (see PROGRESS.md); this table only holds what a technician physically
    scoops from a jar.

    The LIMS records insertion and measurement only. Judging a QC result —
    pass, fail, warning limits, z-scores — is QC Sentinel's job on export;
    there is deliberately no verdict vocabulary anywhere in this system.
    """

    __tablename__ = "qc_material"
    __table_args__ = (
        CheckConstraint(
            "certified_au_value_g_t IS NULL OR certified_au_value_g_t >= 0",
            name="certified_au_non_negative",
        ),
        CheckConstraint(
            "certified_au_uncertainty_g_t IS NULL OR certified_au_uncertainty_g_t > 0",
            name="certified_au_uncertainty_positive",
        ),
    )

    id: Mapped[IdPk]
    name: Mapped[str] = mapped_column(String(100), unique=True)
    qc_type: Mapped[QcMaterialType] = mapped_column(
        _enum(QcMaterialType, "qc_material_type"), index=True
    )
    lot_number: Mapped[str | None] = mapped_column(String(100))

    #: Certified grade and its symmetric uncertainty, both ``g/t``. Null for
    #: blanks — see the class docstring.
    certified_au_value_g_t: Mapped[Decimal | None] = mapped_column(Numeric)
    certified_au_uncertainty_g_t: Mapped[Decimal | None] = mapped_column(Numeric)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text)


class Batch(Base, TimestampMixin):
    """One furnace run: a tray of crucibles fired together.

    Mutable — ``status`` advances in place through
    :mod:`msa_lims.domain.batch_lifecycle`'s linear state machine, matching
    how ``sample.status`` advances. Not append-only: unlike a result or a
    certificate, a batch is not a statement the lab makes to a client, it is
    the lab's own record of a physical event in progress. See
    ``BatchStatus``'s docstring for why ``IN_FUSION`` onward is effectively
    frozen in practice even though the schema does not enforce that lock with
    a grant.
    """

    __tablename__ = "batch"

    id: Mapped[IdPk]
    batch_number: Mapped[str] = mapped_column(String(30), unique=True)
    """Lab-assigned, e.g. 'BATCH-2026-0042'."""
    status: Mapped[BatchStatus] = mapped_column(_enum(BatchStatus, "batch_status"), index=True)
    opened_by_id: Mapped[int] = mapped_column(ForeignKey("lab_user.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    crucibles: Mapped[list[Crucible]] = relationship(back_populates="batch")


class Crucible(Base, TimestampMixin):
    """One assay unit within a batch: one sample, one position, one flux charge.

    Mutable, matching ``Batch`` — its status is bulk-advanced in lockstep
    with the batch's for the stages a furnace run moves every crucible
    through together (see
    :func:`msa_lims.domain.batch_lifecycle.bulk_crucible_status`), while
    parting and weighing are per-crucible acts with their own write paths
    (see ``batches/service.py``): ``PARTED`` records the lead button, prill,
    and parting acid; ``WEIGHED`` records the final gold-bead weighing.

    ``flux_recipe_id`` lives here, not on ``Batch``: one furnace load
    routinely fires a silicate core sample beside a sulfide one, and each
    needs its own recipe. A batch is a shared furnace slot, not a shared
    formula.

    The six reagent columns are the *scaled* charge for this crucible's own
    ``sample_weight_g`` — computed once at charge time by
    :func:`msa_lims.domain.flux.scale_flux_charge` and stored, not
    recomputed from the recipe on every read. This mirrors
    ``fire_assay_result``'s "store what was actually weighed" precedent: if
    the recipe is edited afterward, an already-charged crucible still shows
    what a technician actually weighed out. The four measurement columns
    follow the identical discipline from the other end of the run — each is
    written once, when the physical act happened, and never recomputed or
    overwritten; a result naming this crucible reads them back rather than
    being retyped a second number that could disagree.
    """

    __tablename__ = "crucible"
    __table_args__ = (
        UniqueConstraint("batch_id", "position_row", "position_col", name="batch_position"),
        UniqueConstraint("batch_id", "sample_id", name="batch_sample"),
        CheckConstraint("position_row > 0", name="position_row_positive"),
        CheckConstraint("position_col > 0", name="position_col_positive"),
        CheckConstraint("sample_weight_g > 0", name="sample_weight_positive"),
        CheckConstraint(
            "(sample_id IS NOT NULL) <> (qc_material_id IS NOT NULL)",
            name="exactly_one_of_sample_and_qc_material",
        ),
        CheckConstraint("lead_button_weight_mg > 0", name="lead_button_weight_positive"),
        CheckConstraint("prill_weight_mg > 0", name="prill_weight_positive"),
        CheckConstraint("parting_acid_volume_ml > 0", name="parting_acid_volume_positive"),
        CheckConstraint("gold_bead_mg >= 0", name="gold_bead_non_negative"),
    )

    id: Mapped[IdPk]
    batch_id: Mapped[int] = mapped_column(ForeignKey("batch.id"), index=True)
    #: The sample this crucible assays — or, for a QC insertion, null and
    #: ``qc_material_id`` names the inserted material instead. The CHECK
    #: constraint enforces exactly one of the two being set.
    sample_id: Mapped[int | None] = mapped_column(ForeignKey("sample.id"), index=True)
    qc_material_id: Mapped[int | None] = mapped_column(ForeignKey("qc_material.id"), index=True)
    flux_recipe_id: Mapped[int] = mapped_column(ForeignKey("flux_recipe.id"))

    position_row: Mapped[int] = mapped_column(Integer)
    position_col: Mapped[int] = mapped_column(Integer)
    status: Mapped[CrucibleStatus] = mapped_column(
        _enum(CrucibleStatus, "crucible_status"), index=True
    )

    sample_weight_g: Mapped[Decimal] = mapped_column(Numeric)
    litharge_g: Mapped[Decimal] = mapped_column(Numeric)
    soda_ash_g: Mapped[Decimal] = mapped_column(Numeric)
    borax_g: Mapped[Decimal] = mapped_column(Numeric)
    silica_g: Mapped[Decimal] = mapped_column(Numeric)
    flour_g: Mapped[Decimal] = mapped_column(Numeric)
    nitre_g: Mapped[Decimal] = mapped_column(Numeric)

    # Per-crucible measurements, null until the corresponding act is
    # recorded. See the class docstring: each is stored once, at the moment
    # of the physical act it witnesses.
    lead_button_weight_mg: Mapped[Decimal | None] = mapped_column(Numeric)
    prill_weight_mg: Mapped[Decimal | None] = mapped_column(Numeric)
    parting_acid_volume_ml: Mapped[Decimal | None] = mapped_column(Numeric)
    parted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    gold_bead_mg: Mapped[Decimal | None] = mapped_column(Numeric)
    weighed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    charged_by_id: Mapped[int] = mapped_column(ForeignKey("lab_user.id"))
    charged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    batch: Mapped[Batch] = relationship(back_populates="crucibles")
