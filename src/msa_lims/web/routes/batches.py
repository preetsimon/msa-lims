"""Furnace batching: opening a batch, charging crucibles, firing it through."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, status

from msa_lims.batches.service import (
    BatchInput,
    BatchService,
    CrucibleChargeInput,
    CruciblePartingInput,
    CrucibleWeighingInput,
    get_batch_detail,
    list_batches,
)
from msa_lims.domain.enums import DuplicateInsertionType
from msa_lims.qc_dossiers.service import (
    build_qc_dossier,
    dossier_payload,
    persist_dossier,
)
from msa_lims.web.deps import ActorDep, InternalActorDep, LabUserDep, SessionDep, SettingsDep
from msa_lims.web.schemas import (
    BatchCreate,
    BatchDetailOut,
    BatchOut,
    BatchStatusUpdate,
    CrucibleChargeCreate,
    CrucibleOut,
    CruciblePartingCreate,
    CrucibleSlotOut,
    CrucibleWeighingCreate,
    QcDossierOut,
)

router = APIRouter(prefix="/api", tags=["batches"])


def _service(session: SessionDep, settings: SettingsDep) -> BatchService:
    return BatchService(
        session, furnace_rows=settings.furnace_rows, furnace_columns=settings.furnace_columns
    )


@router.post("/batches", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def create_batch(
    body: BatchCreate,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    opened_by: LabUserDep,
) -> BatchOut:
    service = _service(session, settings)
    batch = service.create_batch(
        BatchInput(opened_at=body.opened_at, notes=body.notes),
        opened_by=opened_by,
        actor_role=actor.role,
    )
    session.commit()
    return BatchOut.from_model(batch)


@router.post(
    "/batches/{batch_id}/crucibles",
    response_model=CrucibleOut,
    status_code=status.HTTP_201_CREATED,
)
def charge_crucible(
    batch_id: int,
    body: CrucibleChargeCreate,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    charged_by: LabUserDep,
) -> CrucibleOut:
    service = _service(session, settings)
    crucible = service.charge_crucible(
        CrucibleChargeInput(
            batch_id=batch_id,
            sample_id=body.sample_id,
            qc_material_id=body.qc_material_id,
            insertion_type=DuplicateInsertionType(body.insertion_type)
            if body.insertion_type is not None
            else None,
            flux_recipe_id=body.flux_recipe_id,
            position_row=body.position_row,
            position_col=body.position_col,
            sample_weight_g=body.sample_weight_g,
            charged_at=body.charged_at,
            notes=body.notes,
        ),
        charged_by=charged_by,
        actor_role=actor.role,
    )
    session.commit()
    return CrucibleOut.from_model(crucible)


@router.post(
    "/batches/{batch_id}/crucibles/{crucible_id}/parting",
    response_model=CrucibleOut,
)
def record_crucible_parting(
    batch_id: int,
    crucible_id: int,
    body: CruciblePartingCreate,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    parted_by: LabUserDep,
) -> CrucibleOut:
    service = _service(session, settings)
    crucible = service.record_parting(
        batch_id,
        crucible_id,
        CruciblePartingInput(
            lead_button_weight_mg=body.lead_button_weight_mg,
            prill_weight_mg=body.prill_weight_mg,
            parting_acid_volume_ml=body.parting_acid_volume_ml,
            parted_at=body.parted_at,
        ),
        parted_by=parted_by,
        actor_role=actor.role,
    )
    session.commit()
    return CrucibleOut.from_model(crucible)


@router.post(
    "/batches/{batch_id}/crucibles/{crucible_id}/weighing",
    response_model=CrucibleOut,
)
def record_crucible_weighing(
    batch_id: int,
    crucible_id: int,
    body: CrucibleWeighingCreate,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    weighed_by: LabUserDep,
) -> CrucibleOut:
    service = _service(session, settings)
    crucible = service.record_weighing(
        batch_id,
        crucible_id,
        CrucibleWeighingInput(gold_bead_mg=body.gold_bead_mg, weighed_at=body.weighed_at),
        weighed_by=weighed_by,
        actor_role=actor.role,
    )
    session.commit()
    return CrucibleOut.from_model(crucible)


@router.patch("/batches/{batch_id}/status", response_model=BatchOut)
def advance_batch_status(
    batch_id: int,
    body: BatchStatusUpdate,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    advanced_by: LabUserDep,
) -> BatchOut:
    service = _service(session, settings)
    batch = service.advance_status(
        batch_id, target=body.status, advanced_by=advanced_by, actor_role=actor.role
    )
    session.commit()
    return BatchOut.from_model(batch)


@router.get("/batches", response_model=list[BatchOut])
def read_batches(
    session: SessionDep,
    actor: InternalActorDep,
    limit: int = 100,
) -> list[BatchOut]:
    return [BatchOut.from_model(batch) for batch in list_batches(session, limit=limit)]


@router.get("/batches/{batch_id}", response_model=BatchDetailOut)
def read_batch(
    batch_id: int, session: SessionDep, settings: SettingsDep, actor: InternalActorDep
) -> BatchDetailOut:
    detail = get_batch_detail(session, batch_id)
    return BatchDetailOut.from_model(
        detail.batch,
        crucibles=[
            CrucibleSlotOut.from_model(
                slot.crucible,
                sample_label=slot.sample_label,
                qc_material_name=slot.qc_material_name,
                qc_material_type=slot.qc_material_type,
            )
            for slot in detail.crucibles
        ],
        furnace_rows=settings.furnace_rows,
        furnace_columns=settings.furnace_columns,
    )


@router.get("/batches/{batch_id}/qc-dossier", response_model=QcDossierOut)
def read_batch_qc_dossier(
    batch_id: int,
    session: SessionDep,
    settings: SettingsDep,
    actor: InternalActorDep,
    reviewer: LabUserDep,
) -> QcDossierOut:
    """This completed batch's sealed QC dossier — audit idea #5's contract.

    Nested under the batch like parting and weighing: a dossier is one view
    of one batch, not an entity of its own. Generation is idempotent and
    content-addressed; fetching twice without new measurements returns the
    same seal and writes nothing new (see `qc_dossiers/service.py`). The
    threshold flags are advisory — recording that a blank came back above the
    lab's line; judging what that means is QC Sentinel's job.
    """
    dossier = build_qc_dossier(
        session,
        batch_id=batch_id,
        blank_threshold_g_t=Decimal(settings.blank_max_grade_g_t),
        max_duplicate_rpd_percent=Decimal(settings.max_duplicate_rpd_percent),
    )
    seal = persist_dossier(session, dossier, actor_id=reviewer.id)
    session.commit()
    return QcDossierOut.from_payload(dossier_payload(dossier), seal=seal)
