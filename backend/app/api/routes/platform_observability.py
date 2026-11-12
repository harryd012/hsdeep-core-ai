"""
GET /api/platform/observability/summary - this process's platform metrics as
JSON, for the superuser-only Platform Observability page.

Platform internals, not tenant data, so it is superuser-only
(``require_admin``), like the other platform-admin surfaces. It reads the
in-process Prometheus registry (see app/services/platform_metrics_summary.py
for scope and limits) and never touches the database. ``/metrics`` stays the
machine interface for Prometheus; this is the human summary.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import tracing
from app.core.auth import Principal, require_admin
from app.core.config import get_settings
from app.core.db import get_db
from app.core.shared_rate_limit import SharedRateLimiter
from app.services import platform_correlation, platform_logs, platform_traces
from app.services.audit_log_service import _safe_cell
from app.services.audit_service import AuditService
from app.services.platform_infrastructure import fetch_infrastructure
from app.services.platform_metrics_summary import build_summary
from app.services.platform_health import evaluate, fetch_alert_rules, fetch_pipeline
from app.services.platform_timeseries import ALL_GROUPS, DEFAULT_RANGE, RANGES, fetch_timeseries
from app.services.prometheus_targets import fetch_targets

# Log and trace queries are written to the security-audit log category.
audit_logger = logging.getLogger("app.services.audit.platform_observability")

router = APIRouter(prefix="/platform/observability", tags=["platform-admin"])


@router.get("/summary")
async def platform_observability_summary(
    request: Request, _: Principal = Depends(require_admin)
) -> dict[str, Any]:
    return build_summary(version=request.app.version, runtime_mode=get_settings().runtime_mode)


@router.get("/prometheus")
async def platform_prometheus_targets(_: Principal = Depends(require_admin)) -> dict[str, Any]:
    """Prometheus's own view of every HSDEEP scrape target (all roles), or
    ``configured: false`` when PROMETHEUS_URL is not set."""
    return await fetch_targets(get_settings().prometheus_url)


@router.get("/health")
async def platform_health(_: Principal = Depends(require_admin)) -> dict[str, Any]:
    """Per-service platform health (HEALTHY / DEGRADED / WARNING / CRITICAL /
    UNKNOWN) from real telemetry, with the rule behind each status."""
    s = get_settings()
    return await evaluate(s.prometheus_url, s.grafana_url, s.alertmanager_url)


@router.get("/alerts")
async def platform_alert_rules(_: Principal = Depends(require_admin)) -> dict[str, Any]:
    """Prometheus alerting rules and their current state (inactive / pending /
    firing), with the active alerts under each."""
    return await fetch_alert_rules(get_settings().prometheus_url)


@router.get("/timeseries")
async def platform_timeseries(
    range_key: str = Query(DEFAULT_RANGE, alias="range", pattern="^(" + "|".join(RANGES) + ")$"),
    group: list[str] | None = Query(
        None, description="Catalogue groups to read. Omitted = the Phase 21 groups (http, collection, scheduler, alerts).",
    ),
    _: Principal = Depends(require_admin),
) -> dict[str, Any]:
    """The fixed platform metric catalogue over 1h / 6h / 24h / 7d, from
    Prometheus, for the page's native charts. The browser picks a range and
    catalogue groups, never a query."""
    if group and any(g not in ALL_GROUPS for g in group):
        raise HTTPException(status_code=422, detail=f"group must be one of: {', '.join(ALL_GROUPS)}")
    return await fetch_timeseries(get_settings().prometheus_url, range_key, groups=tuple(group) if group else None)


# ---------------------------------------------------------------------------
# Phase 22: host and infrastructure, logs, traces, correlation
# ---------------------------------------------------------------------------
#
# All superuser-only, like the routes above. Every backend (Prometheus, Loki,
# Tempo) is queried server-side from validated filters; the browser never
# sends PromQL, LogQL or TraceQL. Log and trace queries are rate limited per
# user and written to the security-audit log; exports and cross-tenant
# lookups are also recorded in the audit trail.

_query_limiter: SharedRateLimiter | None = None


async def _rate_limit(principal: Principal) -> None:
    global _query_limiter
    limit = get_settings().observability_queries_per_minute
    if limit <= 0:
        return
    if _query_limiter is None or _query_limiter.limit != limit:
        _query_limiter = SharedRateLimiter("hsdeep:obsq:", limit, 60)
    if not await _query_limiter.acquire(str(principal.user_id)):
        raise HTTPException(status_code=429, detail="Too many observability queries. Wait a minute and try again.",
                            headers={"Retry-After": "60"})


def _log_query(principal: Principal, action: str, **fields: Any) -> None:
    audit_logger.info(
        "platform_observability_query action=%s", action,
        extra={"audit_action": action, "actor_user_id": str(principal.user_id),
               **{k: v for k, v in fields.items() if v not in (None, "", [], ())}},
    )


async def _audit(db: AsyncSession, request: Request, principal: Principal, action: str, description: str,
                 context: dict[str, Any]) -> None:
    await AuditService(db).record(
        tenant_id=principal.tenant_id, action=action, actor_id=principal.user_id, actor_email=principal.email,
        entity_type="platform_observability", description=description,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:255] or None,
        context={k: v for k, v in context.items() if v not in (None, "", [], ())},
    )
    await db.commit()


def _window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    try:
        return platform_logs.window(since, until, max_hours=get_settings().observability_max_window_hours)
    except platform_logs.FilterInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/infrastructure")
async def platform_infrastructure(
    instance: str | None = Query(None, max_length=128, description="One scrape target (host) to show."),
    _: Principal = Depends(require_admin),
) -> dict[str, Any]:
    """Current telemetry of the HSDEEP platform host: CPU, memory, disk,
    network, containers, PostgreSQL, Redis and GPU, each with an explicit
    state (HEALTHY ... NO DATA / NOT AVAILABLE) and the reason for it."""
    return await fetch_infrastructure(get_settings().prometheus_url, instance)


@router.get("/pipeline")
async def platform_telemetry_pipeline(_: Principal = Depends(require_admin)) -> dict[str, Any]:
    """Whether logs and traces can flow: the OpenTelemetry Collector, Loki and
    Tempo, and whether this backend process is exporting to the collector."""
    s = get_settings()
    return await fetch_pipeline(s.otel_collector_health_url, s.loki_url, s.tempo_url, exporting=tracing.exporting())


def _log_filters(
    since: datetime | None, until: datetime | None, severity: list[str] | None, service: str | None,
    component: str | None, category: str | None, tenant_id: str | None, environment: str | None,
    trace_id: str | None, request_id: str | None, incident_id: str | None, collection_run_id: str | None,
    provider_id: str | None, q: str | None,
) -> platform_logs.LogFilters:
    start, end = _window(since, until)
    try:
        return platform_logs.make_filters(
            since=start, until=end, severities=severity, service=service, component=component, category=category,
            tenant_id=tenant_id, environment=environment, trace_id=trace_id, request_id=request_id,
            incident_id=incident_id, collection_run_id=collection_run_id, provider_id=provider_id, q=q,
        )
    except platform_logs.FilterInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _log_filter_params(
    since: datetime | None = None,
    until: datetime | None = None,
    severity: list[str] | None = Query(None, description="TRACE, DEBUG, INFO, WARN, ERROR, FATAL"),
    service: str | None = Query(None, max_length=128),
    component: str | None = Query(None, max_length=128),
    category: str | None = Query(None, max_length=32),
    tenant_id: str | None = Query(None, max_length=36),
    environment: str | None = Query(None, max_length=32),
    trace_id: str | None = Query(None, max_length=32),
    request_id: str | None = Query(None, max_length=128),
    incident_id: str | None = Query(None, max_length=36),
    collection_run_id: str | None = Query(None, max_length=36),
    provider_id: str | None = Query(None, max_length=36),
    q: str | None = Query(None, max_length=platform_logs.MAX_KEYWORD_CHARS, description="Text in the log line"),
) -> platform_logs.LogFilters:
    return _log_filters(since, until, severity, service, component, category, tenant_id, environment, trace_id,
                        request_id, incident_id, collection_run_id, provider_id, q)


def _filter_summary(f: platform_logs.LogFilters) -> dict[str, Any]:
    return {"since": f.since.isoformat(), "until": f.until.isoformat(), "severities": list(f.severities),
            "service": f.service, "component": f.component, "category": f.category, "tenant_filter": f.tenant_id,
            "environment": f.environment, "trace_id": f.trace_id, "request_id": f.request_id,
            "incident_id": f.incident_id, "collection_run_id": f.collection_run_id, "provider_id": f.provider_id,
            # The keyword itself is not recorded (it may be sensitive); its presence is.
            "keyword": bool(f.q)}


@router.get("/logs")
async def platform_logs_search(
    request: Request,
    filters: platform_logs.LogFilters = Depends(_log_filter_params),
    limit: int = Query(platform_logs.DEFAULT_LIMIT, ge=1, le=platform_logs.MAX_LIMIT),
    cursor: str | None = Query(None, max_length=20, description="next_cursor of the previous page"),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """One page of platform log lines, newest first, from Loki."""
    await _rate_limit(principal)
    s = get_settings()
    try:
        result = await platform_logs.search(s.loki_url, filters, limit=limit, cursor=cursor,
                                            timeout=s.observability_query_timeout_seconds)
    except platform_logs.FilterInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _log_query(principal, "logs.search", returned=len(result["items"]), **_filter_summary(filters))
    # Looking into one tenant's lines from the platform view is recorded.
    if filters.tenant_id and filters.tenant_id != str(principal.tenant_id) and not cursor:
        await _audit(db, request, principal, "platform_observability.logs.tenant_search",
                     "Searched platform logs filtered to another tenant", _filter_summary(filters))
    return result


@router.get("/logs/volume")
async def platform_logs_volume(
    filters: platform_logs.LogFilters = Depends(_log_filter_params),
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    """Log lines per time bucket by severity (the stacked volume chart)."""
    await _rate_limit(principal)
    s = get_settings()
    return await platform_logs.volume(s.loki_url, filters, timeout=s.observability_query_timeout_seconds)


@router.get("/logs/facets")
async def platform_logs_facets(
    since: datetime | None = None, until: datetime | None = None,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    """Services and environments that logged in the window, plus the fixed
    severity and category lists, for the filter controls."""
    await _rate_limit(principal)
    start, end = _window(since, until)
    s = get_settings()
    return await platform_logs.facets(s.loki_url, platform_logs.LogFilters(since=start, until=end),
                                      timeout=s.observability_query_timeout_seconds)


@router.get("/logs/export")
async def platform_logs_export(
    request: Request,
    filters: platform_logs.LogFilters = Depends(_log_filter_params),
    fmt: str = Query("csv", alias="format", pattern="^(csv|ndjson)$"),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The filtered lines as CSV or NDJSON, capped at 5,000 lines, redacted.
    Every export is recorded in the audit trail."""
    await _rate_limit(principal)
    s = get_settings()
    if not s.loki_url:
        raise HTTPException(status_code=503, detail="Log search is not configured (LOKI_URL).")
    lines, error, truncated = await platform_logs.export_lines(s.loki_url, filters,
                                                               timeout=s.observability_query_timeout_seconds)
    if error:
        raise HTTPException(status_code=502, detail=f"Loki did not answer ({error}).")
    await _audit(db, request, principal, "platform_observability.logs.export",
                 f"Exported {len(lines)} platform log lines as {fmt}",
                 {**_filter_summary(filters), "lines": len(lines), "truncated": truncated, "format": fmt})
    _log_query(principal, "logs.export", returned=len(lines), **_filter_summary(filters))
    stamp = filters.until.strftime("%Y%m%dT%H%M%SZ")
    headers = {"X-Export-Truncated": "true" if truncated else "false", "X-Export-Lines": str(len(lines)),
               "Cache-Control": "no-store"}
    if fmt == "ndjson":
        body = "".join(json.dumps({k: line.get(k) for k in platform_logs.EXPORT_COLUMNS} | {
            "exception": line.get("exception"), "attributes": line.get("attributes")}, default=str) + "\n" for line in lines)
        headers["Content-Disposition"] = f'attachment; filename="hsdeep-platform-logs-{stamp}.ndjson"'
        return Response(content=body, media_type="application/x-ndjson", headers=headers)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(platform_logs.EXPORT_COLUMNS)
    for line in lines:
        writer.writerow([_safe_cell(line.get(k)) for k in platform_logs.EXPORT_COLUMNS])
    headers["Content-Disposition"] = f'attachment; filename="hsdeep-platform-logs-{stamp}.csv"'
    return Response(content=buffer.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/traces")
async def platform_traces_search(
    request: Request,
    since: datetime | None = None,
    until: datetime | None = None,
    service: str | None = Query(None, max_length=128),
    operation: str | None = Query(None, max_length=200, description="Exact span name"),
    environment: str | None = Query(None, max_length=32),
    tenant_id: str | None = Query(None, max_length=36),
    incident_id: str | None = Query(None, max_length=36),
    collection_run_id: str | None = Query(None, max_length=36),
    min_duration_ms: int | None = Query(None, ge=0, le=3_600_000),
    errors_only: bool = False,
    limit: int = Query(platform_traces.DEFAULT_LIMIT, ge=1, le=platform_traces.MAX_LIMIT),
    principal: Principal = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Platform traces in a window, newest first, from Tempo."""
    await _rate_limit(principal)
    start, end = _window(since, until)
    try:
        filters = platform_traces.make_filters(
            since=start, until=end, service=service, operation=operation, environment=environment,
            tenant_id=tenant_id, incident_id=incident_id, collection_run_id=collection_run_id,
            min_duration_ms=min_duration_ms, errors_only=errors_only,
        )
    except platform_logs.FilterInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    s = get_settings()
    result = await platform_traces.search(s.tempo_url, filters, limit=limit,
                                          timeout=s.observability_query_timeout_seconds)
    summary = {"since": start.isoformat(), "until": end.isoformat(), "service": filters.service,
               "operation": filters.operation, "tenant_filter": filters.tenant_id, "incident_id": filters.incident_id,
               "collection_run_id": filters.collection_run_id, "min_duration_ms": filters.min_duration_ms,
               "errors_only": filters.errors_only or None}
    _log_query(principal, "traces.search", returned=len(result["items"]), **summary)
    if filters.tenant_id and filters.tenant_id != str(principal.tenant_id):
        await _audit(db, request, principal, "platform_observability.traces.tenant_search",
                     "Searched platform traces filtered to another tenant", summary)
    return result


def _trace_id(trace_id: str) -> str:
    try:
        return platform_traces.valid_trace_id(trace_id)
    except platform_logs.FilterInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/traces/{trace_id}")
async def platform_trace(trace_id: str, principal: Principal = Depends(require_admin)) -> dict[str, Any]:
    """Every span of one platform trace (the waterfall). ``found: false``
    when Tempo has no such trace; that is an answer, not an error."""
    await _rate_limit(principal)
    s = get_settings()
    trace_id = _trace_id(trace_id)
    result = await platform_traces.get_trace(s.tempo_url, trace_id, timeout=s.observability_query_timeout_seconds)
    _log_query(principal, "traces.get", trace_id=trace_id, found=result["found"])
    return result


@router.get("/correlation/{trace_id}")
async def platform_correlation_for_trace(
    trace_id: str, principal: Principal = Depends(require_admin), db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """What is linked to one trace by identifier: its logs, and the alerts,
    incidents and collection runs named on its spans. Metrics are offered for
    the same time window and labelled as time-aligned, not linked."""
    await _rate_limit(principal)
    s = get_settings()
    trace_id = _trace_id(trace_id)
    result = await platform_correlation.correlate(
        trace_id, tempo_url=s.tempo_url, loki_url=s.loki_url, db=db,
        max_window_hours=s.observability_max_window_hours, timeout=s.observability_query_timeout_seconds,
    )
    _log_query(principal, "correlation.get", trace_id=trace_id,
               links={k: v["state"] for k, v in result["links"].items()})
    return result
