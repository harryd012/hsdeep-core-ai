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

from fastapi import APIRouter, Depends, Request

from app.core.auth import Principal, require_admin
from app.core.config import get_settings
from app.services.platform_metrics_summary import build_summary
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
