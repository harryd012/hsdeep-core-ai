"""
Dashboard API.

NOTE(auth): same interim pattern as Monitoring/Alerts (see monitoring.py,
alerts.py) - tenant_id is an explicit query parameter on every endpoint
because there is no authenticated-principal dependency yet. Once the Auth
module exists, tenant_id should be derived from the current user/session
instead.

Unlike alerts.py/monitoring.py, this router intentionally has NO router-level
prefix. It exposes five distinct top-level path groups
(/dashboard/summary, /cloud/status, /network/status, /automation/status,
/ai/insights) that don't share a common prefix, so each route declares its
full path and the router is registered in main.py with prefix="/api" only.
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
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.sensor_repository import SensorRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.dashboard import (
    AIInsights,
    AutomationStatus,
    CloudStatus,
    DashboardSummary,
    NetworkStatus,
)
from app.services.ai_insight_service import AIInsightService
from app.services.automation_status_service import AutomationStatusService
from app.services.dashboard_service import DashboardService

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
