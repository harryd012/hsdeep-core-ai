"""
Dashboard API.

NOTE(auth): every endpoint here takes its tenant from the authenticated
principal via `Depends(get_tenant_id)` - the caller cannot choose a tenant,
and `?tenant_id=` is not an accepted selector anywhere in this module.
(The earlier interim "tenant_id is a query parameter" note was stale: the
auth module has existed for several phases, and `get_tenant_id` is what
every route below actually uses.)

Unlike alerts.py/monitoring.py, this router intentionally has NO router-level
prefix. It exposes several distinct top-level path groups
(/dashboard/summary, /dashboard/operational-intelligence, /cloud/status,
/network/status, /automation/status, /ai/insights) that don't share a common
prefix, so each route declares its full path and the router is registered in
main.py with prefix="/api" only.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository
from app.repositories.task_repository import TaskRepository
from app.schemas.dashboard import (
    AIInsights,
    AutomationStatus,
    CloudOperations,
    CloudStatus,
    CommandCenterBrief,
    DashboardSummary,
    NetworkStatus,
    OperationalIntelligence,
)
from app.services.ai_insight_service import AIInsightService
from app.services.automation_status_service import AutomationStatusService
from app.repositories.incident_analytics_repository import IncidentAnalyticsRepository
from app.schemas.incident import IncidentSeverity
from app.schemas.incident_analytics import IncidentAnalytics
from app.services.dashboard_service import DashboardService
from app.services.incident_analytics_service import IncidentAnalyticsService
from app.repositories.site_health_repository import SiteHealthRepository
from app.schemas.site_health import SiteHealth
from app.services.site_health_service import SiteHealthService
from app.repositories.explorer_hosts_repository import ExplorerHostsRepository, HostFilters
from app.schemas.explorer_hosts import HostHealth, HostsExplorer
from app.services.explorer_hosts_service import ExplorerHostsService
from app.services.cloud_operations_service import CloudOperationsService
from app.services.operational_intelligence_service import (
    OperationalIntelligenceService,
)

router = APIRouter(tags=["dashboard"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_dashboard_service(db: AsyncSession = Depends(get_db)) -> DashboardService:
    return DashboardService(
        site_repo=SiteRepository(db),
        device_repo=DeviceRepository(db),
        source_repo=MonitoringSourceRepository(db),
        sensor_repo=SensorRepository(db),
    )


def get_automation_status_service(
    db: AsyncSession = Depends(get_db),
) -> AutomationStatusService:
    return AutomationStatusService(
        source_repo=MonitoringSourceRepository(db),
        run_repo=CollectorRunRepository(db),
    )


def get_ai_insight_service(db: AsyncSession = Depends(get_db)) -> AIInsightService:
    return AIInsightService(
        alert_repo=AlertRepository(db),
        sensor_repo=SensorRepository(db),
        run_repo=CollectorRunRepository(db),
    )


def get_operational_intelligence_service(
    db: AsyncSession = Depends(get_db),
) -> OperationalIntelligenceService:
    return OperationalIntelligenceService(
        source_repo=MonitoringSourceRepository(db),
        run_repo=CollectorRunRepository(db),
        sensor_repo=SensorRepository(db),
        device_repo=DeviceRepository(db),
        alert_repo=AlertRepository(db),
        task_repo=TaskRepository(db),
        incident_repo=IncidentRepository(db),
    )


def get_cloud_operations_service(
    db: AsyncSession = Depends(get_db),
) -> CloudOperationsService:
    return CloudOperationsService(
        device_repo=DeviceRepository(db),
        incident_repo=IncidentRepository(db),
    )


def get_incident_analytics_service(
    db: AsyncSession = Depends(get_db),
) -> IncidentAnalyticsService:
    return IncidentAnalyticsService(repo=IncidentAnalyticsRepository(db))


def get_site_health_service(
    db: AsyncSession = Depends(get_db),
) -> SiteHealthService:
    return SiteHealthService(repo=SiteHealthRepository(db))


def get_explorer_hosts_service(
    db: AsyncSession = Depends(get_db),
) -> ExplorerHostsService:
    return ExplorerHostsService(repo=ExplorerHostsRepository(db))


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def dashboard_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: DashboardService = Depends(get_dashboard_service),
):
    return await service.summary(tenant_id)


@router.get("/cloud/status", response_model=CloudStatus)
async def cloud_status(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: DashboardService = Depends(get_dashboard_service),
):
    return await service.cloud_status(tenant_id)


@router.get("/network/status", response_model=NetworkStatus)
async def network_status(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: DashboardService = Depends(get_dashboard_service),
):
    return await service.network_status(tenant_id)


@router.get("/automation/status", response_model=AutomationStatus)
async def automation_status(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AutomationStatusService = Depends(get_automation_status_service),
):
    return await service.status(tenant_id)


@router.get("/ai/insights", response_model=AIInsights)
async def ai_insights(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: AIInsightService = Depends(get_ai_insight_service),
):
    return await service.insights(tenant_id)


@router.get(
    "/dashboard/operational-intelligence",
    response_model=OperationalIntelligence,
    summary="Operational Intelligence KPI snapshot (PHASE 6A)",
)
async def dashboard_operational_intelligence(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: OperationalIntelligenceService = Depends(
        get_operational_intelligence_service
    ),
):
    """The compact Operational Intelligence snapshot for the caller's tenant.

    ONE request returns the whole KPI payload, so the UI issues a single call
    instead of one per card. Read-only aggregation over the canonical
    monitoring data path; see OperationalIntelligenceService for the query
    budget and the "no denominator means null, not 0%" rule.

    Deliberately NOT behind `_active_sub_guard`: this is a read, and per the
    comment above that guard, reads stay open for past-due tenants.
    """
    return await service.snapshot(tenant_id)


@router.get(
    "/dashboard/command-center-brief",
    response_model=CommandCenterBrief,
    summary="Command Center brief (PHASE 6A / SPRINT P1)",
)
async def command_center_brief(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: OperationalIntelligenceService = Depends(get_operational_intelligence_service),
):
    """Command Center brief for one tenant.

    Returns what needs the operator's attention now, the estate's health
    buckets, per-source collection health, response KPIs and hourly trends —
    all from persisted columns. Read-only; the tenant is resolved server-side
    from the authenticated principal and cannot be overridden by the caller.
    """
    return await service.command_center_brief(tenant_id)


@router.get(
    "/dashboard/cloud-operations",
    response_model=CloudOperations,
    summary="Cloud Operations KPI snapshot (6 cards: 3 measured + 3 unavailable)",
)
async def dashboard_cloud_operations(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: CloudOperationsService = Depends(get_cloud_operations_service),
):
    """Tenant-scoped cloud-operations KPI snapshot.

    Returns 6 KPI cards:
      * cloud_resources — measured count of persisted cloud_resource devices
      * resource_health — measured device state/freshness buckets (online/offline/stale/unknown/total)
      * active_incidents — measured active incident count (open+ack+investigating)
      * cloud_spend — unavailable (no billing integration)
      * policy_compliance — unavailable (no policy-assessment integration)
      * backup_readiness — unavailable (no backup/recovery evidence)

    Tenant is resolved server-side from the authenticated principal; callers
    cannot override it. Unavailable cards carry an explicit reason string and
    null value — never a fabricated zero.
    """
    return await service.get(tenant_id)


@router.get(
    "/dashboard/incident-analytics",
    response_model=IncidentAnalytics,
    summary="Incident analytics for the Executive Command Center charts",
)
async def dashboard_incident_analytics(
    days: int = Query(7, ge=1, le=90, description="Trend/severity/duration window in UTC days"),
    site_id: uuid.UUID | None = Query(None, description="Only incidents attributed to this site"),
    severity: IncidentSeverity | None = Query(None, description="Only incidents of this severity"),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: IncidentAnalyticsService = Depends(get_incident_analytics_service),
):
    """Opened-vs-resolved trend, severity mix, open-incident aging, weekday x
    hour heatmap, resolved-duration distribution (median/P90/P95) and top
    affected sites - ONE read, eight SQL aggregates, no rows loaded into Python.

    Tenant is resolved server-side from the authenticated principal and cannot
    be overridden. A read, so deliberately NOT behind `_active_sub_guard`
    (reads stay open for past-due tenants, like the other dashboard reads).
    """
    return await service.snapshot(tenant_id, days=days, site_id=site_id, severity=severity)


@router.get(
    "/dashboard/site-health",
    response_model=SiteHealth,
    summary="Per-site device health + active incidents (Sites honeycomb)",
)
async def dashboard_site_health(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: SiteHealthService = Depends(get_site_health_service),
):
    """One row per site: device health in the brief's buckets (same rule), the
    site's active / critical incident counts and its derived worst state,
    worst first. At most three aggregate statements regardless of estate size.

    Tenant is resolved server-side from the authenticated principal and cannot
    be overridden. A read, so deliberately NOT behind `_active_sub_guard`.
    """
    return await service.snapshot(tenant_id)


@router.get(
    "/explorer/hosts",
    response_model=HostsExplorer,
    summary="Hosts & Devices Explorer: filtered table + facet counts",
)
async def explorer_hosts(
    search: str | None = Query(None, max_length=100),
    health: HostHealth | None = Query(None),
    status: str | None = Query(None, max_length=50),
    device_type: str | None = Query(None, max_length=100),
    vendor: str | None = Query(None, max_length=100),
    criticality: str | None = Query(None, max_length=20),
    environment: str | None = Query(None, max_length=20),
    site_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: ExplorerHostsService = Depends(get_explorer_hosts_service),
):
    """Dynatrace-Hosts-style explorer read: a health-sorted host page plus
    facet counts for health / status / type / vendor / criticality /
    environment. Health is derived from each host's open alerts (see
    schemas/explorer_hosts.py). Tenant comes from the authenticated principal
    and cannot be overridden. A read, so not behind `_active_sub_guard`.
    (Lives on the dashboard router so it inherits the `/api` prefix without a
    main.py change.)
    """
    filters = HostFilters(
        search=search or None,
        health=health,
        status=status,
        device_type=device_type,
        vendor=vendor,
        criticality=criticality,
        environment=environment,
        site_id=site_id,
    )
    return await service.snapshot(tenant_id, filters, limit=limit, offset=offset)
