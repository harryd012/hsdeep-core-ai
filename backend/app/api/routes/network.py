"""
Network Monitoring Center routes (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P4 slice 1).

  GET /api/network/overview                       interface health, busiest links, errors, VPN tunnels
  GET /api/network/interfaces                     filterable, sortable, paged interface list
  GET /api/network/interfaces/{id}                one interface with its recorded history channels
  GET /api/network/interfaces/{id}/history        traffic, utilisation and errors over a window

Read-only, `monitoring.read`, tenant-scoped (another tenant's interface is 404).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.schemas.network import InterfaceHistoryOut, NetworkInterfaceDetail, NetworkInterfacePage, NetworkOverviewOut
from app.services.monitoring.network_center import SORTS, STATUS_FILTERS, InterfaceNotFound, NetworkCenterService

router = APIRouter(prefix="/network", tags=["network"], dependencies=[Depends(require_permission("monitoring.read"))])


@router.get("/overview", response_model=NetworkOverviewOut)
async def network_overview(tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    return await NetworkCenterService(db).overview(tenant_id)


@router.get("/interfaces", response_model=NetworkInterfacePage)
async def list_interfaces(
    status: str | None = Query(None, pattern=f"^({'|'.join(STATUS_FILTERS)})$"),
    device_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=100),
    sort: str = Query("status", pattern=f"^({'|'.join(SORTS)})$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    items, total = await NetworkCenterService(db).interfaces(
        tenant_id, status=status, device_id=device_id, q=q, sort=sort, limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.get("/interfaces/{interface_id}", response_model=NetworkInterfaceDetail)
async def get_interface(
    interface_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
):
    try:
        return await NetworkCenterService(db).interface(tenant_id, interface_id)
    except InterfaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Interface not found") from exc


@router.get("/interfaces/{interface_id}/history", response_model=InterfaceHistoryOut)
async def interface_history(
    interface_id: uuid.UUID,
    hours: int = Query(24, ge=1, le=720),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await NetworkCenterService(db).history(tenant_id, interface_id, hours=hours)
    except InterfaceNotFound as exc:
        raise HTTPException(status_code=404, detail="Interface not found") from exc
