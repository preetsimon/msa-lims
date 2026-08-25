"""Request and response shapes.

Separate from the ORM models on purpose. The API is a contract with the UI and
with demo scripts; letting SQLAlchemy models serialise themselves would make
every column rename a breaking API change, and would leak columns nobody
outside the database should depend on.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from msa_lims.db.models import (
    Certificate,
    Client,
    DrillHole,
    FireAssayResult,
    Project,
    Sample,
    Submission,
)
from msa_lims.domain.enums import SampleType
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
    gold_bead_mg: Decimal = Field(ge=0, description="Bead weight after parting — gold alone.")
    sample_weight_g: Decimal = Field(gt=0)
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
