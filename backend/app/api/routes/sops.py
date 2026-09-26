"""
SOP Repository / Knowledge Base API (SOP & Runbook Intelligence Engine
sprint).

NOTE(auth): same interim pattern as every other router in this codebase -
tenant_id is an explicit query parameter because there is no
authenticated-principal dependency yet.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.sop_repository import SOPRepository
from app.schemas.sop import (
    SOPCreate,
    SOPDetailOut,
    SOPOut,
    SOPVersionApproveRequest,
    SOPVersionCreate,
    SOPVersionOut,
)
from app.services.sop_service import SOPNotFound, SOPService, SOPVersionNotFound

router = APIRouter(prefix="/sops", tags=["sops"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_sop_service(db: AsyncSession = Depends(get_db)) -> SOPService:
    return SOPService(sop_repo=SOPRepository(db))


@router.get("", response_model=list[SOPOut])
async def list_sops(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    category: str | None = None,
    vendor: str | None = None,
    platform: str | None = None,
    device_type: str | None = None,
    is_active: bool | None = True,
    search: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: SOPService = Depends(get_sop_service),
):
    return await service.list_sops(
        tenant_id,
        category=category,
        vendor=vendor,
        platform=platform,
        device_type=device_type,
        is_active=is_active,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get("/search", response_model=list[SOPOut])
async def search_sops(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    q: str = Query(..., min_length=1),
    limit: int = Query(default=25, ge=1, le=100),
    service: SOPService = Depends(get_sop_service),
):
    """Knowledge Base search deliverable - keyword/tag search."""
    return await service.search(tenant_id, q, limit=limit)


@router.get("/{sop_id}", response_model=SOPDetailOut)
async def get_sop(
    sop_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: SOPService = Depends(get_sop_service),
):
    try:
        sop = await service.get(sop_id, tenant_id)
    except SOPNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    sop_out = SOPOut.model_validate(sop, from_attributes=True)
    versions = [SOPVersionOut.model_validate(v, from_attributes=True) for v in sop.versions]
    return SOPDetailOut(**sop_out.model_dump(), versions=versions)


@router.post("", dependencies=[_active_sub_guard], response_model=SOPOut, status_code=201)
async def create_sop(
    payload: SOPCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: SOPService = Depends(get_sop_service),
):
    sop = await service.create_sop(tenant_id, **payload.model_dump())
    await db.commit()
    return sop


@router.get("/{sop_id}/versions", response_model=list[SOPVersionOut])
async def list_sop_versions(
    sop_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: SOPService = Depends(get_sop_service),
):
    try:
        return await service.list_versions(sop_id, tenant_id)
    except SOPNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{sop_id}/versions", dependencies=[_active_sub_guard], response_model=SOPVersionOut, status_code=201)
async def create_sop_version(
    sop_id: uuid.UUID,
    payload: SOPVersionCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: SOPService = Depends(get_sop_service),
):
    try:
        version = await service.create_version(sop_id, tenant_id, **payload.model_dump())
    except SOPNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return version


@router.post("/{sop_id}/versions/{version_id}/approve", dependencies=[_active_sub_guard], response_model=SOPOut)
async def approve_sop_version(
    sop_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: SOPVersionApproveRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: SOPService = Depends(get_sop_service),
):
    """Manual approval workflow applied to SOP content: approves the
    version AND promotes it to the SOP's current_version_id."""
    try:
        sop = await service.approve_and_promote(sop_id, version_id, tenant_id, approved_by=payload.approved_by)
    except (SOPNotFound, SOPVersionNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return sop
