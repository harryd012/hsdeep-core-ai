"""
Logs Explorer routes (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P7 slice 1).

  GET /api/logs         log lines in a window (newest first), filtered and paged
  GET /api/logs/stats   volume per time bucket by severity, and top hosts / apps / types

Read-only, `monitoring.read`, tenant-scoped. Windows default to the last hour
and are capped at 7 days. Logs arrive through the collector ingest API (the
syslog receiver, `python -m app.edge.syslog_receiver`, or any collector).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.services.logs.explorer import LogExplorer, LogQuery, WindowInvalid, window

router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(require_permission("monitoring.read"))])


class LogLine(BaseModel):
    id: uuid.UUID
    timestamp: datetime
    severity: str
    event_type: str
    message: str
    host: str | None
    app: str | None
    facility: str | None
    syslog_severity: str | None
    device_id: uuid.UUID | None
    device_name: str | None
    attributes: dict


class LogPage(BaseModel):
    since: datetime
    until: datetime
    items: list[LogLine]
    total: int


class FacetValue(BaseModel):
    value: str
    count: int


class HistogramBucket(BaseModel):
    t: datetime
    critical: int
    warning: int
    info: int


class LogStats(BaseModel):
    since: datetime
    until: datetime
    bucket_seconds: int
    histogram: list[HistogramBucket]
    facets: dict[str, list[FacetValue]]


def _query(since, until, q, severity, host, app, device_id, event_type) -> LogQuery:
    try:
        start, end = window(since, until)
    except WindowInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LogQuery(
        since=start, until=end, q=q or None, severities=tuple(dict.fromkeys(severity or [])),
        host=host or None, app=app or None, device_id=device_id, event_type=event_type or None,
    )


_SEVERITY = Query(None, description="critical, warning or info; repeat for several")


@router.get("", response_model=LogPage)
async def search_logs(
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = Query(None, max_length=200),
    severity: list[str] | None = _SEVERITY,
    host: str | None = Query(None, max_length=255),
    app: str | None = Query(None, max_length=100),
    device_id: uuid.UUID | None = None,
    event_type: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=10000),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    if severity and not set(severity) <= {"critical", "warning", "info"}:
        raise HTTPException(status_code=422, detail="severity must be critical, warning or info")
    query = _query(since, until, q, severity, host, app, device_id, event_type)
    items, total = await LogExplorer(db).search(tenant_id, query, limit=limit, offset=offset)
    return {"since": query.since, "until": query.until, "items": items, "total": total}


@router.get("/stats", response_model=LogStats)
async def log_stats(
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = Query(None, max_length=200),
    severity: list[str] | None = _SEVERITY,
    host: str | None = Query(None, max_length=255),
    app: str | None = Query(None, max_length=100),
    device_id: uuid.UUID | None = None,
    event_type: str | None = Query(None, max_length=100),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    if severity and not set(severity) <= {"critical", "warning", "info"}:
        raise HTTPException(status_code=422, detail="severity must be critical, warning or info")
    query = _query(since, until, q, severity, host, app, device_id, event_type)
    return {"since": query.since, "until": query.until, **await LogExplorer(db).stats(tenant_id, query)}
