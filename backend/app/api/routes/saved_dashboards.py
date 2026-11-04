"""
Saved dashboards routes (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P6 slice 2).

  GET    /api/dashboards/widgets        - the widget registry (types, data source, settings schema)
  GET    /api/dashboards                - the caller's dashboards, then shared ones
  POST   /api/dashboards                - create (personal; sharing needs devices.write)
  GET    /api/dashboards/{id}           - one own or shared dashboard
  PATCH  /api/dashboards/{id}           - owner only
  DELETE /api/dashboards/{id}           - owner only

A reader (`dashboard.read`) may keep dashboards: they are personal settings,
like saved host views. Widgets read their data from existing APIs with the
viewer's own permissions. Another user's private or another tenant's dashboard
is a 404.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, require_permission
from app.core.db import get_db
from app.schemas.dashboards import (
    WIDGET_TYPES,
    DashboardCreate,
    DashboardOut,
    DashboardSummary,
    DashboardUpdate,
    WidgetTypeOut,
)
from app.services.dashboard_store import (
    DashboardConflict,
    DashboardNotFound,
    DashboardStore,
    WidgetTargetInvalid,
    dashboard_out,
    dashboard_summary,
)
from app.services.host_inventory_service import Actor

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
_reader = require_permission("dashboard.read")


def _actor(principal: Principal) -> Actor:
    return Actor(tenant_id=principal.tenant_id, user_id=principal.user_id, email=principal.email)


def _require_share_permission(principal: Principal, shared: bool | None) -> None:
    if shared and not principal.has_permission("devices.write"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sharing a dashboard requires devices.write")


@router.get("/widgets", response_model=list[WidgetTypeOut], dependencies=[Depends(_reader)])
async def widget_registry() -> list[WidgetTypeOut]:
    return [
        WidgetTypeOut(type=key, label=label, source=source, settings=model.model_json_schema())
        for key, (label, source, model) in WIDGET_TYPES.items()
    ]


@router.get("", response_model=list[DashboardSummary])
async def list_dashboards(principal: Principal = Depends(_reader), db: AsyncSession = Depends(get_db)):
    actor = _actor(principal)
    return [dashboard_summary(d, actor) for d in await DashboardStore(db).list(actor)]


@router.post("", response_model=DashboardOut, status_code=status.HTTP_201_CREATED)
async def create_dashboard(body: DashboardCreate, principal: Principal = Depends(_reader), db: AsyncSession = Depends(get_db)):
    _require_share_permission(principal, body.shared)
    actor = _actor(principal)
    try:
        d = await DashboardStore(db).create(actor, body)
    except DashboardConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WidgetTargetInvalid as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return dashboard_out(d, actor)


@router.get("/{dashboard_id}", response_model=DashboardOut)
async def get_dashboard(dashboard_id: uuid.UUID, principal: Principal = Depends(_reader), db: AsyncSession = Depends(get_db)):
    actor = _actor(principal)
    try:
        return dashboard_out(await DashboardStore(db).get(actor, dashboard_id), actor)
    except DashboardNotFound as exc:
        raise HTTPException(status_code=404, detail="Dashboard not found") from exc


@router.patch("/{dashboard_id}", response_model=DashboardOut)
async def update_dashboard(
    dashboard_id: uuid.UUID, body: DashboardUpdate, principal: Principal = Depends(_reader), db: AsyncSession = Depends(get_db),
):
    _require_share_permission(principal, body.shared)
    actor = _actor(principal)
    try:
        d = await DashboardStore(db).update(actor, dashboard_id, body)
    except DashboardNotFound as exc:
        raise HTTPException(status_code=404, detail="Dashboard not found") from exc
    except DashboardConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except WidgetTargetInvalid as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return dashboard_out(d, actor)


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dashboard(dashboard_id: uuid.UUID, principal: Principal = Depends(_reader), db: AsyncSession = Depends(get_db)):
    try:
        await DashboardStore(db).delete(_actor(principal), dashboard_id)
    except DashboardNotFound as exc:
        raise HTTPException(status_code=404, detail="Dashboard not found") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
