"""
Traces routes (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P8 slice 1).

  GET /api/traces/services        per-service spans, errors and entry latency
  GET /api/traces                 traces in the window, filtered and paged
  GET /api/traces/service-map     caller -> callee edges between services
  GET /api/traces/{trace_id}      every span of one trace (waterfall)

APM, per service (the name is the rest of the path, so it may contain "/"):

  GET /api/traces/services/{service}/timeseries     requests, errors, latency per bucket
  GET /api/traces/services/{service}/operations     per span name
  GET /api/traces/services/{service}/dependencies   callers and callees
  GET /api/traces/errors                            failed spans grouped by exception

Read-only, `monitoring.read`, tenant-scoped (another tenant's trace is a 404).
Windows default to the last hour and are capped at 7 days. Spans arrive over
OTLP/HTTP at /api/v1/ingest/otlp/v1/traces.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_tenant_id, require_permission
from app.core.db import get_db
from app.services.tracing.query import MAX_TRACE_SPANS, TraceNotFound, TraceQuery, WindowInvalid, window

router = APIRouter(prefix="/traces", tags=["traces"], dependencies=[Depends(require_permission("monitoring.read"))])
_TRACE_ID = re.compile(r"^[0-9a-fA-F]{32}$")


class ServiceStats(BaseModel):
    service: str
    spans: int
    error_spans: int
    traces: int
    entry_spans: int
    error_rate_pct: float
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    last_seen: datetime


class TraceSummary(BaseModel):
    trace_id: str
    start: datetime
    duration_ms: float
    spans: int
    errors: int
    services: list[str]
    root_service: str | None
    root_name: str | None
    #: False when the root span has not arrived (the earliest span stands in).
    root_complete: bool


class TracePage(BaseModel):
    since: datetime
    until: datetime
    items: list[TraceSummary]
    total: int


class ServiceEdge(BaseModel):
    source: str
    target: str
    calls: int
    errors: int
    avg_ms: float
    p95_ms: float | None


class ServiceMap(BaseModel):
    since: datetime
    until: datetime
    services: list[ServiceStats]
    edges: list[ServiceEdge]


class SpanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    span_id: str
    parent_span_id: str | None
    service_name: str
    name: str
    kind: str
    start_time: datetime
    end_time: datetime
    duration_ms: float
    status_code: str
    status_message: str | None
    attributes: dict
    resource_attributes: dict
    events: list


class TraceDetail(BaseModel):
    trace_id: str
    spans: list[SpanOut]
    #: Spans whose parent is not in the trace (not yet received, or dropped by the sender).
    orphans: int
    truncated: bool


class TimeseriesPoint(BaseModel):
    t: datetime
    requests: int
    errors: int
    error_rate_pct: float
    #: None for a bucket with no requests (nothing was measured).
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


class ServiceTimeseries(BaseModel):
    service: str
    since: datetime
    until: datetime
    bucket_seconds: int
    points: list[TimeseriesPoint]


class OperationStats(BaseModel):
    name: str
    #: True when some of its spans are entry spans (server / consumer / root).
    entry: bool
    calls: int
    errors: int
    error_rate_pct: float
    p50_ms: float | None
    p95_ms: float | None
    total_ms: float
    time_share_pct: float


class ServiceOperations(BaseModel):
    service: str
    since: datetime
    until: datetime
    items: list[OperationStats]


class ServiceDependencies(BaseModel):
    service: str
    since: datetime
    until: datetime
    callers: list[ServiceEdge]
    callees: list[ServiceEdge]


class ErrorGroup(BaseModel):
    service: str
    operation: str
    exception_type: str | None
    message: str | None
    count: int
    traces: int
    first_seen: datetime
    last_seen: datetime
    sample_trace_id: str


class ErrorGroups(BaseModel):
    since: datetime
    until: datetime
    items: list[ErrorGroup]


def _window(since, until):
    try:
        return window(since, until)
    except WindowInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/services", response_model=list[ServiceStats])
async def trace_services(
    since: datetime | None = None, until: datetime | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    start, end = _window(since, until)
    return await TraceQuery(db).services(tenant_id, start, end)


@router.get("/service-map", response_model=ServiceMap)
async def service_map(
    since: datetime | None = None, until: datetime | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    start, end = _window(since, until)
    return {"since": start, "until": end, **await TraceQuery(db).service_map(tenant_id, start, end)}


@router.get("", response_model=TracePage)
async def list_traces(
    since: datetime | None = None,
    until: datetime | None = None,
    service: str | None = Query(None, max_length=255),
    errors_only: bool = False,
    min_duration_ms: float | None = Query(None, ge=0),
    q: str | None = Query(None, max_length=200, description="Text in any span name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=10000),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    start, end = _window(since, until)
    items, total = await TraceQuery(db).traces(
        tenant_id, start, end, service=service, errors_only=errors_only, min_duration_ms=min_duration_ms,
        q=q, limit=limit, offset=offset,
    )
    return {"since": start, "until": end, "items": items, "total": total}


@router.get("/services/{service:path}/timeseries", response_model=ServiceTimeseries)
async def service_timeseries(
    service: str, since: datetime | None = None, until: datetime | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    start, end = _window(since, until)
    return {"service": service, "since": start, "until": end, **await TraceQuery(db).timeseries(tenant_id, service, start, end)}


@router.get("/services/{service:path}/operations", response_model=ServiceOperations)
async def service_operations(
    service: str, since: datetime | None = None, until: datetime | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    start, end = _window(since, until)
    return {"service": service, "since": start, "until": end, "items": await TraceQuery(db).operations(tenant_id, service, start, end)}


@router.get("/services/{service:path}/dependencies", response_model=ServiceDependencies)
async def service_dependencies(
    service: str, since: datetime | None = None, until: datetime | None = None,
    tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    start, end = _window(since, until)
    return {"service": service, "since": start, "until": end, **await TraceQuery(db).dependencies(tenant_id, service, start, end)}


@router.get("/errors", response_model=ErrorGroups)
async def trace_errors(
    since: datetime | None = None, until: datetime | None = None,
    service: str | None = Query(None, max_length=255),
    tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
):
    """Failed spans grouped by service, span name and exception, most frequent first (top 100)."""
    start, end = _window(since, until)
    return {"since": start, "until": end, "items": await TraceQuery(db).errors(tenant_id, start, end, service=service)}


@router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: str, tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    if not _TRACE_ID.match(trace_id):
        raise HTTPException(status_code=422, detail="trace id must be 32 hex characters")
    query = TraceQuery(db)
    try:
        spans = await query.trace(tenant_id, trace_id)
    except TraceNotFound as exc:
        raise HTTPException(status_code=404, detail="Trace not found") from exc
    ids = {s.span_id for s in spans}
    return {
        "trace_id": trace_id.lower(),
        "spans": spans,
        "orphans": sum(1 for s in spans if s.parent_span_id and s.parent_span_id not in ids),
        "truncated": len(spans) >= MAX_TRACE_SPANS,
    }
