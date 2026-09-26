"""
Usage metering REST API.

Exposes per-tenant consumption data for the frontend dashboard and
billing system. All endpoints are prefixed with
``/api/v1/tenants/{tenant_id}/usage``.

Reuses the existing tenant resolver and JWT context middleware without
modifying core routing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_current_user, require_permission
from app.core.db import get_db
from app.core.di import get_usage_collector
from app.services.metering.collector import TenantUsageCollector

router = APIRouter(prefix="/api/v1", tags=["Usage Metering"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

from pydantic import BaseModel


class UsageEventCount(BaseModel):
    """Single event type count for a tenant."""

    event_type: str
    count: int


class UsageSummary(BaseModel):
    """Aggregated usage summary for a tenant."""

    tenant_id: str
    total_api_calls: int
    total_alerts_ingested: int
    total_metrics_ingested: int
    total_collector_runs: int
    total_sop_executions: int
    total_workflow_executions: int
    total_auth_logins: int
    grand_total: int
    period_start: str
    period_end: str


class UsageBucketOut(BaseModel):
    """A single hourly usage bucket."""

    event_type: str
    bucket_start: str
    count: int


class UsageEventsOut(BaseModel):
    """Usage data broken into hourly buckets."""

    tenant_id: str
    events: list[UsageBucketOut]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/tenants/{tenant_id}/usage/summary",
    response_model=UsageSummary,
)
async def get_tenant_usage_summary(
    tenant_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    principal: Principal = Depends(require_permission("usage.read")),
    db: AsyncSession = Depends(get_db),
    collector: TenantUsageCollector = Depends(get_usage_collector),
):
    """Return an aggregated usage summary for the given tenant.

    Supports subscription tier validation by exposing per-event-type
    consumption totals over a configurable lookback window.
    """
    # Ensure the requesting principal belongs to the target tenant
    _check_tenant_access(principal, tenant_id)

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    agg = await collector.get_aggregated(tenant_id, db, since=since, until=until)

    return UsageSummary(
        tenant_id=str(tenant_id),
        total_api_calls=agg.get("api.call", 0),
        total_alerts_ingested=agg.get("alert.ingested", 0),
        total_metrics_ingested=agg.get("metric.ingested", 0),
        total_collector_runs=agg.get("collector.run", 0),
        total_sop_executions=agg.get("sop.executed", 0),
        total_workflow_executions=agg.get("workflow.executed", 0),
        total_auth_logins=agg.get("auth.login", 0),
        grand_total=sum(agg.values()),
        period_start=since.isoformat(),
        period_end=until.isoformat(),
    )


@router.get(
    "/tenants/{tenant_id}/usage/events",
    response_model=UsageEventsOut,
)
async def get_tenant_usage_events(
    tenant_id: uuid.UUID,
    event_type: str | None = Query(default=None, description="Filter by event type"),
    days: int = Query(default=7, ge=1, le=90),
    principal: Principal = Depends(require_permission("usage.read")),
    db: AsyncSession = Depends(get_db),
    collector: TenantUsageCollector = Depends(get_usage_collector),
):
    """Return hourly usage buckets for the given tenant.

    Used by the frontend consumption tracker to render time-series charts.
    """
    _check_tenant_access(principal, tenant_id)

    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    rows = await collector.get_usage(
        tenant_id, db, event_type=event_type, since=since, until=until
    )

    events = [
        UsageBucketOut(
            event_type=r.event_type,
            bucket_start=r.bucket_start.isoformat(),
            count=r.count,
        )
        for r in rows
    ]

    return UsageEventsOut(
        tenant_id=str(tenant_id),
        events=events,
        total=sum(e.count for e in events),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_tenant_access(principal: Principal, target_tenant_id: uuid.UUID) -> None:
    """Ensure the principal can access the target tenant's data.

    Superusers can access any tenant; regular users are scoped to their own.
    """
    if not principal.is_superuser and principal.tenant_id != target_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: you can only view usage for your own tenant.",
        )