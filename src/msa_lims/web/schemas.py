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

from msa_lims.db.models import Client, Project, Sample, Submission
from msa_lims.domain.enums import SampleType


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
