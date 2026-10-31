"""Zone API routes."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.zone_repository import ZoneRepository
from app.schemas.zone import ZoneCreate, ZoneRead, ZoneSummary, ZoneUpdate
from app.services.zone_service import ZoneNotFound, ZoneService

router = APIRouter(prefix="/zones", tags=["zones"])


def get_zone_service(session: AsyncSession = Depends(get_db)) -> ZoneService:
    return ZoneService(ZoneRepository(session))


@router.post("/", response_model=ZoneRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission("sites.write"))])
async def create_zone(
    zone_data: ZoneCreate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: None = Depends(require_active_subscription),
    service: ZoneService = Depends(get_zone_service),
):
    return await service.create(
        tenant_id=tenant_id,
        site_id=zone_data.site_id,
        name=zone_data.name,
        code=zone_data.code,
        description=zone_data.description,
        zone_type=zone_data.zone_type,
    )


@router.get("/", response_model=list[ZoneRead])
async def list_zones(
    site_id: Optional[uuid.UUID] = Query(None),
    zone_type: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ZoneService = Depends(get_zone_service),
):
    return await service.list(tenant_id, site_id=site_id, zone_type=zone_type, limit=limit, offset=offset)


@router.get("/summary", response_model=ZoneSummary)
async def zone_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ZoneService = Depends(get_zone_service),
):
    return await service.summary(tenant_id)


@router.get("/{zone_id}", response_model=ZoneRead)
async def get_zone(
    zone_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ZoneService = Depends(get_zone_service),
):
    try:
        return await service.get(zone_id, tenant_id)
    except ZoneNotFound:
        raise HTTPException(status_code=404, detail="Zone not found")


@router.patch("/{zone_id}", response_model=ZoneRead, dependencies=[Depends(require_permission("sites.write"))])
async def update_zone(
    zone_id: uuid.UUID,
    zone_data: ZoneUpdate,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    _: None = Depends(require_active_subscription),
    service: ZoneService = Depends(get_zone_service),
):
    try:
        return await service.update(zone_id, tenant_id, **zone_data.model_dump(exclude_unset=True))
    except ZoneNotFound:
        raise HTTPException(status_code=404, detail="Zone not found")


@router.delete("/{zone_id}", dependencies=[Depends(require_permission("sites.write"))])
async def delete_zone(
    zone_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ZoneService = Depends(get_zone_service),
):
    try:
        await service.delete(zone_id, tenant_id)
        return {"status": "deleted"}
    except ZoneNotFound:
        raise HTTPException(status_code=404, detail="Zone not found")
