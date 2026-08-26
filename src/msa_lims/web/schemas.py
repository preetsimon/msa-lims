"""Request and response shapes.

Separate from the ORM models on purpose. The API is a contract with the UI and
with demo scripts; letting SQLAlchemy models serialise themselves would make
every column rename a breaking API change, and would leak columns nobody
outside the database should depend on.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from msa_lims.db.models import (
    Batch,
    Certificate,
    Client,
    Crucible,
    DrillHole,
    FireAssayResult,
    FluxRecipe,
    Project,
    QcMaterial,
    Sample,
    Submission,
)
from msa_lims.domain.enums import BatchStatus, MatrixType, QcMaterialType, SampleType
from msa_lims.domain.values import MeasuredValue


class ClientCreate(BaseModel):
    code: str = Field(min_length=1, max_length=12, description="e.g. 'MSA'")
    name: str = Field(min_length=1, max_length=200)
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    billing_address: str | None = None


class ClientOut(BaseModel):
    id: int
    code: str
    name: str
    contact_person: str | None
    email: str | None
    phone: str | None
    billing_address: str | None
    is_active: bool

    @classmethod
    def from_model(cls, client: Client) -> ClientOut:
        return cls(
            id=client.id,
            code=client.code,
            name=client.name,
            contact_person=client.contact_person,
            email=client.email,
            phone=client.phone,
            billing_address=client.billing_address,
            is_active=client.is_active,
        )


class ProjectCreate(BaseModel):
    client_id: int
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProjectOut(BaseModel):
    id: int
    client_id: int
    name: str
    description: str | None
    location: str | None
    start_date: date | None
    end_date: date | None

    @classmethod
    def from_model(cls, project: Project) -> ProjectOut:
        return cls(
            id=project.id,
            client_id=project.client_id,
            name=project.name,
            description=project.description,
            location=project.location,
            start_date=project.start_date,
            end_date=project.end_date,
        )


class DrillHoleCreate(BaseModel):
    project_id: int
    hole_id: str = Field(min_length=1, description="e.g. 'MSA-24-001'")
    easting: Decimal | None = None
    northing: Decimal | None = None
    elevation_m: Decimal | None = None
    utm_zone: str | None = None
    total_depth_m: Decimal | None = Field(default=None, gt=0)
    # Mirrors the DB's dip_range and azimuth_range CHECK constraints, so a bad
    # value comes back as a clean 422 rather than a raw IntegrityError.
    dip_degrees: Decimal | None = Field(default=None, ge=-90, le=90)
    azimuth_degrees: Decimal | None = Field(default=None, ge=0, lt=360)
    drilling_method: str | None = None


class DrillHoleOut(BaseModel):
    id: int
    project_id: int
    hole_id: str
    easting: Decimal | None
    northing: Decimal | None
    elevation_m: Decimal | None
    utm_zone: str | None
    total_depth_m: Decimal | None
    dip_degrees: Decimal | None
    azimuth_degrees: Decimal | None
    drilling_method: str | None

    @classmethod
    def from_model(cls, hole: DrillHole) -> DrillHoleOut:
        return cls(
            id=hole.id,
            project_id=hole.project_id,
            hole_id=hole.hole_id,
            easting=hole.easting,
            northing=hole.northing,
            elevation_m=hole.elevation_m,
            utm_zone=hole.utm_zone,
            total_depth_m=hole.total_depth_m,
            dip_degrees=hole.dip_degrees,
            azimuth_degrees=hole.azimuth_degrees,
            drilling_method=hole.drilling_method,
        )


class SampleCreate(BaseModel):
    sample_id: str = Field(
        min_length=1,
        description="e.g. 'MSA-24-001-142.50_144.00' (drill) or 'MSA-24-SO-00417' (surface)",
    )
    sample_type: SampleType
    lithology_code: str | None = None
    alteration_code: str | None = None
    weight_received_g: Decimal | None = Field(default=None, gt=0)
    easting: Decimal | None = None
    northing: Decimal | None = None
    elevation_m: Decimal | None = None
    comments: str | None = None


class SubmissionCreate(BaseModel):
    client_id: int
    project_id: int | None = None
    client_reference: str | None = None
    purchase_order: str | None = None
    received_at: datetime
    declared_sample_count: int | None = Field(default=None, ge=0)
    rush: bool = False
    requested_tat_days: int | None = Field(default=None, gt=0)
    comments: str | None = None
    samples: list[SampleCreate] = Field(min_length=1)


class SampleOut(BaseModel):
    id: int
    sample_id: str
    sample_type: str
    status: str
    drill_hole_id: int | None
    from_depth_m: Decimal | None
    to_depth_m: Decimal | None

    @classmethod
    def from_model(cls, sample: Sample) -> SampleOut:
        return cls(
            id=sample.id,
            sample_id=sample.sample_id,
            sample_type=sample.sample_type.value,
            status=sample.status.value,
            drill_hole_id=sample.drill_hole_id,
            from_depth_m=sample.from_depth_m,
            to_depth_m=sample.to_depth_m,
        )


class CertificateReferenceOut(BaseModel):
    """One certificate that names a sample — not the whole document, just
    enough to link to it via ``GET /api/certificates/{id}``."""

    id: int
    certificate_number: str


class SampleListItemOut(BaseModel):
    """One row of ``GET /api/samples`` — deliberately lighter than
    `SampleDetailOut`: no current result, no certificate list, so listing a
    hundred samples costs one query, not a hundred and one."""

    id: int
    sample_id: str
    sample_type: str
    status: str
    client_name: str
    submission_number: str

    @classmethod
    def from_model(
        cls, sample: Sample, *, client_name: str, submission_number: str
    ) -> SampleListItemOut:
        return cls(
            id=sample.id,
            sample_id=sample.sample_id,
            sample_type=sample.sample_type.value,
            status=sample.status.value,
            client_name=client_name,
            submission_number=submission_number,
        )


class SubmissionOut(BaseModel):
    id: int
    submission_number: str
    client_id: int
    project_id: int | None
    received_at: datetime
    declared_sample_count: int | None
    samples: list[SampleOut]

    @classmethod
    def from_model(cls, submission: Submission) -> SubmissionOut:
        return cls(
            id=submission.id,
            submission_number=submission.submission_number,
            client_id=submission.client_id,
            project_id=submission.project_id,
            received_at=submission.received_at,
            declared_sample_count=submission.declared_sample_count,
            samples=[SampleOut.from_model(sample) for sample in submission.samples],
        )


class MeasuredValueOut(BaseModel):
    """Mirrors ``frontend/src/types.ts``'s ``MeasuredValue`` exactly, so the
    same ``formatMeasured`` helper renders a non-detect correctly the moment
    a screen exists to show one — a censored value is never a null string
    quietly standing in for zero."""

    value: str | None
    detection_limit: str | None
    censored: bool
    unit: str

    @classmethod
    def from_domain(cls, measured: MeasuredValue) -> MeasuredValueOut:
        return cls(
            value=None if measured.value is None else str(measured.value),
            detection_limit=(
                None if measured.detection_limit is None else str(measured.detection_limit)
            ),
            censored=measured.censored,
            unit=measured.unit.value,
        )


class FireAssayResultCreate(BaseModel):
    sample_id: int
    gold_bead_mg: Decimal | None = Field(
        default=None,
        ge=0,
        description=(
            "Bead weight after parting — gold alone. Required unless crucible_id names a "
            "crucible that has been weighed; then its recorded bead is used and this must "
            "be left unset."
        ),
    )
    sample_weight_g: Decimal | None = Field(
        default=None,
        gt=0,
        description=(
            "The portion assayed. Required unless crucible_id names the crucible the "
            "sample was charged into — then its recorded charge is used and this must "
            "be left unset."
        ),
    )
    balance_sensitivity_mg: Decimal | None = Field(default=None, gt=0)
    analysed_at: datetime = Field(
        description="When the weighing happened, not when it was entered."
    )
    notes: str | None = None
    supersedes_id: int | None = Field(
        default=None, description="Set to correct an existing result."
    )
    superseded_reason: str | None = Field(
        default=None, description="Required when supersedes_id is set."
    )
    crucible_id: int | None = Field(
        default=None,
        description=(
            "The crucible this assay came from; its recorded charge is derived as the "
            "portion weight."
        ),
    )


class FireAssayResultOut(BaseModel):
    id: int
    sample_id: int
    method: str
    gold_bead_mg: Decimal
    sample_weight_g: Decimal
    balance_sensitivity_mg: Decimal | None
    au: MeasuredValueOut
    analysed_at: datetime
    supersedes_id: int | None
    superseded_reason: str | None
    notes: str | None
    crucible_id: int | None

    @classmethod
    def from_model(cls, result: FireAssayResult) -> FireAssayResultOut:
        return cls(
            id=result.id,
            sample_id=result.sample_id,
            method=result.method.value,
            gold_bead_mg=result.gold_bead_mg,
            sample_weight_g=result.sample_weight_g,
            balance_sensitivity_mg=result.balance_sensitivity_mg,
            au=MeasuredValueOut(
                value=None if result.au_value is None else str(result.au_value),
                detection_limit=(
                    None if result.au_detection_limit is None else str(result.au_detection_limit)
                ),
                censored=result.au_censored,
                unit=result.au_unit,
            ),
            analysed_at=result.analysed_at,
            supersedes_id=result.supersedes_id,
            superseded_reason=result.superseded_reason,
            notes=result.notes,
            crucible_id=result.crucible_id,
        )


class CertificateCreate(BaseModel):
    client_id: int
    sample_ids: list[int] = Field(min_length=1)
    issued_at: datetime
    notes: str | None = None
    supersedes_id: int | None = Field(
        default=None, description="Set to amend an existing certificate."
    )
    superseded_reason: str | None = Field(
        default=None, description="Required when supersedes_id is set."
    )


class CertifiedSampleOut(BaseModel):
    """One sample a certificate covers, and the specific result it certified —
    see ``certificates/service.py``'s ``CertifiedSampleInfo`` for why this is
    the frozen-at-issuance result, not necessarily the sample's current one."""

    sample_id: int
    sample_label: str
    fire_assay_result_id: int
    method: str
    au: MeasuredValueOut


class CertificateOut(BaseModel):
    id: int
    certificate_number: str
    client_id: int
    issued_by_id: int
    issued_at: datetime
    pdf_sha256: str
    supersedes_id: int | None
    superseded_reason: str | None
    notes: str | None
    samples: list[CertifiedSampleOut]

    @classmethod
    def from_model(
        cls, certificate: Certificate, *, samples: list[CertifiedSampleOut]
    ) -> CertificateOut:
        return cls(
            id=certificate.id,
            certificate_number=certificate.certificate_number,
            client_id=certificate.client_id,
            issued_by_id=certificate.issued_by_id,
            issued_at=certificate.issued_at,
            pdf_sha256=certificate.pdf_sha256,
            supersedes_id=certificate.supersedes_id,
            superseded_reason=certificate.superseded_reason,
            notes=certificate.notes,
            samples=samples,
        )


class SampleDetailOut(BaseModel):
    id: int
    sample_id: str
    sample_type: str
    status: str
    submission_id: int
    drill_hole_id: int | None
    from_depth_m: Decimal | None
    to_depth_m: Decimal | None
    current_result: FireAssayResultOut | None
    certificates: list[CertificateReferenceOut]

    @classmethod
    def from_model(
        cls,
        sample: Sample,
        *,
        current_result: FireAssayResultOut | None,
        certificates: list[CertificateReferenceOut],
    ) -> SampleDetailOut:
        return cls(
            id=sample.id,
            sample_id=sample.sample_id,
            sample_type=sample.sample_type.value,
            status=sample.status.value,
            submission_id=sample.submission_id,
            drill_hole_id=sample.drill_hole_id,
            from_depth_m=sample.from_depth_m,
            to_depth_m=sample.to_depth_m,
            current_result=current_result,
            certificates=certificates,
        )


class SampleStatusUpdate(BaseModel):
    """A bare lifecycle move — see ``sample_lifecycle/service.py``.

    ``target`` is deliberately narrower than the full ``SampleStatus``
    vocabulary: ``in_assay``, ``assayed``, and ``reported`` are each reached
    only by the write path that produces the record making them true
    (charging a crucible, entering a result, issuing a certificate), never by
    a bare status flip here. Naming one of those is refused at this layer,
    before the request even reaches the service.
    """

    target: Literal["in_prep", "ready_for_assay", "rejected"]
    reason: str | None = Field(
        default=None,
        description="Required for rejecting a sample or returning one for re-assay.",
    )


class FluxRecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="e.g. 'Standard Silicate'")
    matrix_type: MatrixType
    nominal_portion_g: Decimal = Field(
        gt=0, description="Sample weight this recipe's amounts are calibrated for."
    )
    litharge_g: Decimal = Field(ge=0)
    soda_ash_g: Decimal = Field(ge=0)
    borax_g: Decimal = Field(ge=0)
    silica_g: Decimal = Field(ge=0)
    flour_g: Decimal = Field(ge=0)
    nitre_g: Decimal = Field(ge=0)


class FluxRecipeOut(BaseModel):
    id: int
    name: str
    matrix_type: str
    nominal_portion_g: Decimal
    litharge_g: Decimal
    soda_ash_g: Decimal
    borax_g: Decimal
    silica_g: Decimal
    flour_g: Decimal
    nitre_g: Decimal
    is_active: bool

    @classmethod
    def from_model(cls, recipe: FluxRecipe) -> FluxRecipeOut:
        return cls(
            id=recipe.id,
            name=recipe.name,
            matrix_type=recipe.matrix_type.value,
            nominal_portion_g=recipe.nominal_portion_g,
            litharge_g=recipe.litharge_g,
            soda_ash_g=recipe.soda_ash_g,
            borax_g=recipe.borax_g,
            silica_g=recipe.silica_g,
            flour_g=recipe.flour_g,
            nitre_g=recipe.nitre_g,
            is_active=recipe.is_active,
        )


class QcMaterialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="e.g. 'OREAS 501d'")
    qc_type: QcMaterialType
    lot_number: str | None = Field(default=None, max_length=100)
    #: Required for a CRM, refused for a blank — a blank is defined by having
    #: no certified grade. *Whether* one is present is enforced in the
    #: service so both sides of that rule are reported together; the bounds
    #: below mirror the database's own CHECK constraints (a grade cannot be
    #: negative, an uncertainty of exactly zero claims a perfectly certain
    #: measurement) so a bad value comes back as a clean 422 rather than a
    #: raw IntegrityError — found by Schemathesis fuzzing the live app,
    #: which hit a CRM with `certified_au_uncertainty_g_t: 0` and crashed
    #: straight into the database's CHECK constraint.
    certified_au_value_g_t: Decimal | None = Field(default=None, ge=0)
    certified_au_uncertainty_g_t: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class QcMaterialOut(BaseModel):
    id: int
    name: str
    qc_type: str
    lot_number: str | None
    certified_au_value_g_t: Decimal | None
    certified_au_uncertainty_g_t: Decimal | None
    is_active: bool

    @classmethod
    def from_model(cls, material: QcMaterial) -> QcMaterialOut:
        return cls(
            id=material.id,
            name=material.name,
            qc_type=material.qc_type.value,
            lot_number=material.lot_number,
            certified_au_value_g_t=material.certified_au_value_g_t,
            certified_au_uncertainty_g_t=material.certified_au_uncertainty_g_t,
            is_active=material.is_active,
        )


class BatchCreate(BaseModel):
    opened_at: datetime
    notes: str | None = None


class BatchStatusUpdate(BaseModel):
    status: BatchStatus


class CrucibleChargeCreate(BaseModel):
    #: The sample this crucible assays — or, for a QC insertion, leave unset
    #: and name ``qc_material_id`` instead. Exactly one of the two; the service
    #: refuses both and neither alike.
    sample_id: int | None = None
    qc_material_id: int | None = None
    flux_recipe_id: int
    position_row: int = Field(gt=0)
    position_col: int = Field(gt=0)
    sample_weight_g: Decimal = Field(gt=0)
    charged_at: datetime
    notes: str | None = None


class CruciblePartingCreate(BaseModel):
    """The measurements taken while parting one cupelled crucible."""

    lead_button_weight_mg: Decimal = Field(gt=0, description="Lead button from the fusion.")
    prill_weight_mg: Decimal = Field(gt=0, description="Doré bead left by cupellation.")
    parting_acid_volume_ml: Decimal = Field(gt=0, description="Acid used to part the prill.")
    parted_at: datetime = Field(description="When the parting happened, not when it was entered.")


class CrucibleWeighingCreate(BaseModel):
    """The final gold-bead weighing for one parted crucible."""

    gold_bead_mg: Decimal = Field(ge=0, description="Gold alone, after parting.")
    weighed_at: datetime = Field(description="When the weighing happened, not when entered.")


class CrucibleOut(BaseModel):
    id: int
    batch_id: int
    #: Null for a QC insertion, where ``qc_material_id`` names what was
    #: charged instead — the two are mutually exclusive at the database.
    sample_id: int | None
    qc_material_id: int | None
    flux_recipe_id: int
    position_row: int
    position_col: int
    status: str
    sample_weight_g: Decimal
    litharge_g: Decimal
    soda_ash_g: Decimal
    borax_g: Decimal
    silica_g: Decimal
    flour_g: Decimal
    nitre_g: Decimal
    lead_button_weight_mg: Decimal | None
    prill_weight_mg: Decimal | None
    parting_acid_volume_ml: Decimal | None
    parted_at: datetime | None
    gold_bead_mg: Decimal | None
    weighed_at: datetime | None
    charged_at: datetime
    notes: str | None

    @classmethod
    def from_model(cls, crucible: Crucible) -> CrucibleOut:
        return cls(
            id=crucible.id,
            batch_id=crucible.batch_id,
            sample_id=crucible.sample_id,
            qc_material_id=crucible.qc_material_id,
            flux_recipe_id=crucible.flux_recipe_id,
            position_row=crucible.position_row,
            position_col=crucible.position_col,
            status=crucible.status.value,
            sample_weight_g=crucible.sample_weight_g,
            litharge_g=crucible.litharge_g,
            soda_ash_g=crucible.soda_ash_g,
            borax_g=crucible.borax_g,
            silica_g=crucible.silica_g,
            flour_g=crucible.flour_g,
            nitre_g=crucible.nitre_g,
            lead_button_weight_mg=crucible.lead_button_weight_mg,
            prill_weight_mg=crucible.prill_weight_mg,
            parting_acid_volume_ml=crucible.parting_acid_volume_ml,
            parted_at=crucible.parted_at,
            gold_bead_mg=crucible.gold_bead_mg,
            weighed_at=crucible.weighed_at,
            charged_at=crucible.charged_at,
            notes=crucible.notes,
        )


class BatchOut(BaseModel):
    id: int
    batch_number: str
    status: str
    opened_by_id: int
    opened_at: datetime
    notes: str | None

    @classmethod
    def from_model(cls, batch: Batch) -> BatchOut:
        return cls(
            id=batch.id,
            batch_number=batch.batch_number,
            status=batch.status.value,
            opened_by_id=batch.opened_by_id,
            opened_at=batch.opened_at,
            notes=batch.notes,
        )


class BatchDetailOut(BaseModel):
    id: int
    batch_number: str
    status: str
    opened_by_id: int
    opened_at: datetime
    notes: str | None
    crucibles: list[CrucibleOut]

    @classmethod
    def from_model(cls, batch: Batch, *, crucibles: list[CrucibleOut]) -> BatchDetailOut:
        return cls(
            id=batch.id,
            batch_number=batch.batch_number,
            status=batch.status.value,
            opened_by_id=batch.opened_by_id,
            opened_at=batch.opened_at,
            notes=batch.notes,
            crucibles=crucibles,
        )
