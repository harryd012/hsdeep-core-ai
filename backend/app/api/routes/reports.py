"""
Observability reports (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P11 slice 1).

  GET /api/reports/availability   per-device availability from recorded reachability checks
  GET /api/reports/sensors        per-sensor reading statistics and time in alert
  GET /api/reports/interfaces     per-interface traffic, utilisation and error rates

Read-only, `monitoring.read`, tenant-scoped. `since` / `until` default to the
last 7 days; the window is capped at 90 days.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.services.reports.observability_reports import ObservabilityReports, WindowInvalid, window

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_permission("monitoring.read"))])


def _window(since, until):
    try:
        return window(since, until)
    except WindowInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/availability")
async def availability_report(since: datetime | None = None, until: datetime | None = None,
                              tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)) -> dict:
    start, end = _window(since, until)
    return {"since": start, "until": end, **await ObservabilityReports(db).availability(tenant_id, start, end)}


@router.get("/sensors")
async def sensors_report(since: datetime | None = None, until: datetime | None = None,
                         metric_type: str | None = Query(None, max_length=100),
                         tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)) -> dict:
    start, end = _window(since, until)
    return {"since": start, "until": end, **await ObservabilityReports(db).sensors(tenant_id, start, end, metric_type=metric_type)}


@router.get("/interfaces")
async def interfaces_report(since: datetime | None = None, until: datetime | None = None,
                            tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)) -> dict:
    start, end = _window(since, until)
    return {"since": start, "until": end, **await ObservabilityReports(db).interfaces(tenant_id, start, end)}
