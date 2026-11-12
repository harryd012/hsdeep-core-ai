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

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.auth import Principal, require_admin
from app.core.config import get_settings
from app.services.platform_metrics_summary import build_summary
from app.services.platform_health import evaluate, fetch_alert_rules
from app.services.platform_timeseries import DEFAULT_RANGE, RANGES, fetch_timeseries
from app.services.prometheus_targets import fetch_targets

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
    _: Principal = Depends(require_admin),
) -> dict[str, Any]:
    """The fixed platform metric catalogue over 1h / 6h / 24h / 7d, from
    Prometheus, for the page's native charts. The browser picks a range,
    never a query."""
    return await fetch_timeseries(get_settings().prometheus_url, range_key)
