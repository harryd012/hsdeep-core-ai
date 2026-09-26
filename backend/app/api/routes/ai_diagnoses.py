"""AI Diagnosis API routes."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.ai_diagnosis import AIDiagnosis
from app.repositories.ai_diagnosis_repository import AiDiagnosisRepository
from app.schemas.ai_diagnosis import AiDiagnosisCreate, AiDiagnosisRead, AiDiagnosisSummary, AiDiagnosisUpdate
from app.services.ai_diagnosis_service import AiDiagnosisNotFound, AiDiagnosisService

router = APIRouter(prefix="/ai/diagnoses", tags=["ai-diagnoses"])


def get_ai_diagnosis_service(session: AsyncSession = Depends(get_db)) -> AiDiagnosisService:
    return AiDiagnosisService(AiDiagnosisRepository(session))


@router.post("/", response_model=AiDiagnosisRead, status_code=status.HTTP_201_CREATED)
async def create_diagnosis(
    diagnosis_data: AiDiagnosisCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: None = Depends(require_active_subscription),
    service: AiDiagnosisService = Depends(get_ai_diagnosis_service),
):
    return await service.create(
        tenant_id=tenant_id,
        noc_event_id=diagnosis_data.noc_event_id,
        requested_by=diagnosis_data.requested_by,
    )


@router.get("/", response_model=list[AiDiagnosisRead])
async def list_diagnoses(
    status: Optional[str] = Query(None),
    noc_event_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AiDiagnosisService = Depends(get_ai_diagnosis_service),
):
    return await service.list(tenant_id, status=status, limit=limit, offset=offset)


@router.get("/summary", response_model=AiDiagnosisSummary)
async def diagnosis_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AiDiagnosisService = Depends(get_ai_diagnosis_service),
):
    return await service.summary(tenant_id)


@router.get("/{diagnosis_id}", response_model=AiDiagnosisRead)
async def get_diagnosis(
    diagnosis_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AiDiagnosisService = Depends(get_ai_diagnosis_service),
):
    try:
        return await service.get(diagnosis_id, tenant_id)
    except AiDiagnosisNotFound:
        raise HTTPException(status_code=404, detail="AI diagnosis not found")


@router.get("/by-noc-event/{noc_event_id}", response_model=AiDiagnosisRead)
async def get_diagnosis_by_noc_event(
    noc_event_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AiDiagnosisService = Depends(get_ai_diagnosis_service),
):
    diagnosis = await service.get_by_noc_event(noc_event_id, tenant_id)
    if diagnosis is None:
        raise HTTPException(status_code=404, detail="No diagnosis found for this NOC event")
    return diagnosis


@router.patch("/{diagnosis_id}", response_model=AiDiagnosisRead)
async def update_diagnosis(
    diagnosis_id: uuid.UUID,
    diagnosis_data: AiDiagnosisUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: None = Depends(require_active_subscription),
    service: AiDiagnosisService = Depends(get_ai_diagnosis_service),
):
    try:
        return await service.update(diagnosis_id, tenant_id, **diagnosis_data.model_dump(exclude_unset=True))
    except AiDiagnosisNotFound:
        raise HTTPException(status_code=404, detail="AI diagnosis not found")


@router.delete("/{diagnosis_id}")
async def delete_diagnosis(
    diagnosis_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AiDiagnosisService = Depends(get_ai_diagnosis_service),
):
    try:
        await service.delete(diagnosis_id, tenant_id)
        return {"status": "deleted"}
    except AiDiagnosisNotFound:
        raise HTTPException(status_code=404, detail="AI diagnosis not found")
