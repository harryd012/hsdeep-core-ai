"""
Alert Engine API.

NOTE(auth): same interim pattern as Monitoring (see monitoring.py) -
tenant_id is an explicit query parameter on every endpoint because there is
no authenticated-principal dependency yet. Once the Auth module exists,
tenant_id should be derived from the current user/session instead.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.auth import get_actor_user_id, get_tenant_id, require_permission
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.alert_repository import AlertRepository
from app.schemas.alert import (
    AlertAcknowledge,
    AlertHistoryOut,
    AlertOut,
    AlertResolve,
    AlertSeverity,
    AlertStatistics,
    AlertStatus,
    AlertSummary,
)
from app.services.alert_service import AlertNotFound, AlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_alert_service(db: AsyncSession = Depends(get_db)) -> AlertService:
    return AlertService(alert_repo=AlertRepository(db))


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    severity: AlertSeverity | None = None,
    status: AlertStatus | None = None,
    metric_type: str | None = None,
    category: str | None = None,
    source: str | None = None,
    incident_id: uuid.UUID | None = None,
    include_suppressed: bool = Query(
        default=False,
        description="By default, alerts suppressed as a child of a detected "
        "root cause are excluded from this list (Active Alerts view). Set "
        "true to include them (e.g. for an incident's full member list).",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: AlertService = Depends(get_alert_service),
):
    return await service.list_alerts(
        tenant_id,
        site_id=site_id,
        device_id=device_id,
        severity=severity,
        status=status,
        metric_type=metric_type,
        category=category,
        source=source,
        incident_id=incident_id,
        is_suppressed=None if include_suppressed else False,
        limit=limit,
        offset=offset,
    )


@router.get("/summary", response_model=AlertSummary)
async def alert_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AlertService = Depends(get_alert_service),
):
    return await service.summary(tenant_id)


@router.get("/statistics", response_model=AlertStatistics)
async def alert_statistics(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AlertService = Depends(get_alert_service),
):
    return await service.statistics(tenant_id)


@router.get("/{alert_id}/history", response_model=list[AlertHistoryOut])
async def alert_history(
    alert_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AlertService = Depends(get_alert_service),
):
    try:
        return await service.get_history(alert_id, tenant_id)
    except AlertNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(
    alert_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AlertService = Depends(get_alert_service),
):
    try:
        return await service.get(alert_id, tenant_id)
    except AlertNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{alert_id}/acknowledge", dependencies=[Depends(require_permission("alerts.acknowledge")), _active_sub_guard], response_model=AlertOut)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    payload: AlertAcknowledge = AlertAcknowledge(),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    actor_id: uuid.UUID | None = Depends(get_actor_user_id),
    db: AsyncSession = Depends(get_db),
    service: AlertService = Depends(get_alert_service),
):
    try:
        alert = await service.acknowledge(alert_id, tenant_id, actor_id)
    except AlertNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return alert


@router.post("/{alert_id}/resolve", dependencies=[Depends(require_permission("alerts.resolve")), _active_sub_guard], response_model=AlertOut)
async def resolve_alert(
    alert_id: uuid.UUID,
    payload: AlertResolve,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: AlertService = Depends(get_alert_service),
):
    try:
        alert = await service.resolve(alert_id, tenant_id, payload.resolved_by)
    except AlertNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return alert