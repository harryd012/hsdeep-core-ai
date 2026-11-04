"""
Hosts explorer: saved views and bulk host actions (observability plan P2 slice 2).

Routes (mounted under /api):
  GET    /explorer/hosts/views            - own views + views shared in the tenant
  POST   /explorer/hosts/views            - create (shared=true needs devices.write)
  PATCH  /explorer/hosts/views/{id}       - owner only
  DELETE /explorer/hosts/views/{id}       - owner only
  GET    /explorer/hosts/vocabulary       - allowed criticality / environment values
  POST   /explorer/hosts/bulk             - classify hosts (devices.write)
  POST   /explorer/hosts/bulk/monitoring  - pause / resume monitoring (monitoring.update)
  GET    /explorer/hosts/resources        - server fleet: CPU / memory / disk / load / uptime (P5)

The tenant always comes from the authenticated principal. Another tenant's (or,
for writes, another user's) view is a 404. Saved views are personal settings: a
reader (devices.read) may keep them, and they are not behind the subscription
gate. Bulk actions change hosts: each route carries its own write permission and
the subscription gate.
"""
from __future__ import annotations

import uuid

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.schemas.host_inventory import (
    CLASSIFY_ACTIONS,
    HOST_CRITICALITIES,
    HOST_ENVIRONMENTS,
    MAX_BULK_HOSTS,
    MONITORING_ACTIONS,
    HostBulkRequest,
    HostBulkResult,
    HostViewCreate,
    HostViewOut,
    HostViewUpdate,
)
from app.schemas.host_resources import FleetSummaryOut, ServerFleetOut, ServerResourcesOut
from app.services.host_resources_service import PLATFORMS, HostResourcesService
from app.services.host_inventory_service import (
    Actor,
    HostBulkService,
    HostSavedViewService,
    HostViewConflict,
    HostViewNotFound,
    TooManySensors,
    view_out,
)

router = APIRouter(prefix="/explorer/hosts", tags=["explorer"])

_active_sub_guard = Depends(require_active_subscription)


def _actor(principal: Principal) -> Actor:
    return Actor(tenant_id=principal.tenant_id, user_id=principal.user_id, email=principal.email)


def _require_share_permission(principal: Principal, shared: bool | None) -> None:
    if shared and not principal.has_permission("devices.write"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sharing a view requires devices.write")


@router.get("/views", response_model=list[HostViewOut])
async def list_host_views(
    principal: Principal = Depends(require_permission("devices.read")),
    db: AsyncSession = Depends(get_db),
):
    actor = _actor(principal)
    return [view_out(v, actor) for v in await HostSavedViewService(db).list(actor)]


@router.post("/views", response_model=HostViewOut, status_code=status.HTTP_201_CREATED)
async def create_host_view(
    body: HostViewCreate,
    principal: Principal = Depends(require_permission("devices.read")),
    db: AsyncSession = Depends(get_db),
):
    _require_share_permission(principal, body.shared)
    actor = _actor(principal)
    try:
        view = await HostSavedViewService(db).create(actor, body)
    except HostViewConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return view_out(view, actor)


@router.patch("/views/{view_id}", response_model=HostViewOut)
async def update_host_view(
    view_id: uuid.UUID,
    body: HostViewUpdate,
    principal: Principal = Depends(require_permission("devices.read")),
    db: AsyncSession = Depends(get_db),
):
    _require_share_permission(principal, body.shared)
    actor = _actor(principal)
    try:
        view = await HostSavedViewService(db).update(actor, view_id, body)
    except HostViewNotFound as exc:
        raise HTTPException(status_code=404, detail="View not found") from exc
    except HostViewConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return view_out(view, actor)


@router.delete("/views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_host_view(
    view_id: uuid.UUID,
    principal: Principal = Depends(require_permission("devices.read")),
    db: AsyncSession = Depends(get_db),
):
    try:
        await HostSavedViewService(db).delete(_actor(principal), view_id)
    except HostViewNotFound as exc:
        raise HTTPException(status_code=404, detail="View not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/vocabulary", dependencies=[Depends(require_permission("devices.read"))])
async def host_vocabulary() -> dict:
    """The values bulk classification accepts, so the UI never hard-codes them."""
    return {"criticality": list(HOST_CRITICALITIES), "environment": list(HOST_ENVIRONMENTS), "max_bulk_hosts": MAX_BULK_HOSTS}


@router.post("/bulk", response_model=HostBulkResult, dependencies=[_active_sub_guard])
async def bulk_classify_hosts(
    body: HostBulkRequest,
    principal: Principal = Depends(require_permission("devices.write")),
    db: AsyncSession = Depends(get_db),
):
    """Set criticality / environment or add / remove tags on up to 200 hosts of
    the caller's tenant. Every changed host is audited; ids outside the tenant
    are returned in `not_found`."""
    if body.action not in CLASSIFY_ACTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{body.action} is sent to /bulk/monitoring")
    return await _run_bulk(body, principal, db)


@router.post("/bulk/monitoring", response_model=HostBulkResult, dependencies=[_active_sub_guard])
async def bulk_host_monitoring(
    body: HostBulkRequest,
    principal: Principal = Depends(require_permission("monitoring.update")),
    db: AsyncSession = Depends(get_db),
):
    """Pause or resume every sensor of up to 200 hosts through the sensor
    lifecycle. Every changed host is audited."""
    if body.action not in MONITORING_ACTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{body.action} is sent to /bulk")
    return await _run_bulk(body, principal, db)


async def _run_bulk(body: HostBulkRequest, principal: Principal, db: AsyncSession) -> HostBulkResult:
    try:
        body.check()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    try:
        return await HostBulkService(db).run(_actor(principal), body)
    except TooManySensors as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/resources", response_model=ServerFleetOut)
async def server_fleet_resources(
    platform: str | None = Query(None, pattern=f"^({'|'.join(PLATFORMS)})$"),
    principal: Principal = Depends(require_permission("devices.read")),
    db: AsyncSession = Depends(get_db),
) -> ServerFleetOut:
    """Every server reporting host resources, worst first, with a fleet summary
    (the summary always covers all platforms; `platform` filters the rows)."""
    summary, rows = await HostResourcesService(db).fleet(principal.tenant_id, platform=platform)
    return ServerFleetOut(
        summary=FleetSummaryOut(**asdict(summary)),
        servers=[ServerResourcesOut(**asdict(r)) for r in rows],
    )
