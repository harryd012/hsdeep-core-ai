"""
Device Inventory & Observability API.

Read-only surface over devices, interfaces, metrics, events, and collector
run history (see design's API Surface table). There is no
create/update/delete here - those entities are populated exclusively by the
collector/monitoring pipeline, never directly through this API.

NOTE(auth): same interim pattern as Monitoring/Alerts (see monitoring.py,
alerts.py) - tenant_id is an explicit query parameter on every list-level
endpoint because there is no authenticated-principal dependency yet. Once
the Auth module exists, tenant_id should be derived from the current
user/session instead.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.sensor_repository import SensorRepository
from app.schemas.alert import AlertOut
from app.schemas.collector_run import CollectorRunOut
from app.schemas.device import DeviceOut
from app.schemas.event import EventOut
from app.schemas.interface import InterfaceOut
from app.schemas.metric import MetricOut
from app.schemas.monitoring import SensorOut
from app.schemas.pagination import Page
from app.schemas.sensor import SensorDetailOut

router = APIRouter(tags=["devices"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_device_repository(db: AsyncSession = Depends(get_db)) -> DeviceRepository:
    return DeviceRepository(db)


def get_interface_repository(db: AsyncSession = Depends(get_db)) -> InterfaceRepository:
    return InterfaceRepository(db)


def get_metric_repository(db: AsyncSession = Depends(get_db)) -> MetricRepository:
    return MetricRepository(db)


def get_event_repository(db: AsyncSession = Depends(get_db)) -> EventRepository:
    return EventRepository(db)


def get_collector_run_repository(db: AsyncSession = Depends(get_db)) -> CollectorRunRepository:
    return CollectorRunRepository(db)


def get_sensor_repository(db: AsyncSession = Depends(get_db)) -> SensorRepository:
    return SensorRepository(db)


def get_alert_repository(db: AsyncSession = Depends(get_db)) -> AlertRepository:
    return AlertRepository(db)


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    device_type: str | None = None,
    vendor: str | None = None,
    status: str | None = None,
    device_repo: DeviceRepository = Depends(get_device_repository),
):
    return await device_repo.list(
        tenant_id,
        site_id=site_id,
        device_type=device_type,
        vendor=vendor,
        status=status,
    )


@router.get("/devices/paginated", response_model=Page[DeviceOut])
async def list_devices_paginated(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    device_type: str | None = None,
    vendor: str | None = None,
    status: str | None = None,
    search: str | None = None,
    sort_by: str = "name",
    sort_dir: str = "asc",
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    device_repo: DeviceRepository = Depends(get_device_repository),
):
    """Paginated counterpart to list_devices() above, for tables that need a
    total count for server-side pagination (Infrastructure page, etc). Added
    as a new route rather than changing list_devices()'s response shape,
    since frontend/src/lib/api.ts's listDevices() already depends on
    GET /devices returning a bare array - changing that in place would be a
    breaking change for existing callers."""
    items = await device_repo.list(
        tenant_id,
        site_id=site_id,
        device_type=device_type,
        vendor=vendor,
        status=status,
        search=search,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    total = await device_repo.count(
        tenant_id,
        site_id=site_id,
        device_type=device_type,
        vendor=vendor,
        status=status,
        search=search,
    )
    return Page[DeviceOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    device_repo: DeviceRepository = Depends(get_device_repository),
):
    device = await device_repo.get(device_id, tenant_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return device


@router.get("/devices/{device_id}/interfaces", response_model=list[InterfaceOut])
async def list_device_interfaces(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    device_repo: DeviceRepository = Depends(get_device_repository),
    interface_repo: InterfaceRepository = Depends(get_interface_repository),
):
    # 404 first, to enforce tenant isolation before exposing interfaces for
    # a device that doesn't belong to this tenant.
    device = await device_repo.get(device_id, tenant_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return await interface_repo.list_for_device(device_id)


@router.get("/devices/{device_id}/metrics", response_model=list[MetricOut])
async def list_device_metrics(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    metric_type: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    device_repo: DeviceRepository = Depends(get_device_repository),
    metric_repo: MetricRepository = Depends(get_metric_repository),
):
    device = await device_repo.get(device_id, tenant_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return await metric_repo.list_for_device(
        device_id, metric_type=metric_type, since=since, limit=limit
    )


@router.get("/devices/{device_id}/sensors", response_model=list[SensorOut])
async def list_device_sensors(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    device_repo: DeviceRepository = Depends(get_device_repository),
    sensor_repo: SensorRepository = Depends(get_sensor_repository),
):
    # 404 first, to enforce tenant isolation before exposing sensors for a
    # device that doesn't belong to this tenant (mirrors
    # list_device_interfaces() above).
    device = await device_repo.get(device_id, tenant_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return await sensor_repo.list_for_device(device_id)


@router.get("/devices/{device_id}/alerts", response_model=list[AlertOut])
async def list_device_alerts(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    device_repo: DeviceRepository = Depends(get_device_repository),
    alert_repo: AlertRepository = Depends(get_alert_repository),
):
    device = await device_repo.get(device_id, tenant_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return await alert_repo.list(tenant_id, device_id=device_id, limit=limit, offset=offset)


@router.get("/devices/{device_id}/events", response_model=list[EventOut])
async def list_device_events(
    device_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    device_repo: DeviceRepository = Depends(get_device_repository),
    event_repo: EventRepository = Depends(get_event_repository),
):
    # The existing top-level /events route already supports device_id as a
    # filter - this device-scoped path exists for the Device Details page,
    # which needs a device-nested URL rather than a query-filtered one.
    device = await device_repo.get(device_id, tenant_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    return await event_repo.list(tenant_id, device_id=device_id, limit=limit, offset=offset)


@router.get("/events", response_model=list[EventOut])
async def list_events(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    device_id: uuid.UUID | None = None,
    severity: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    event_repo: EventRepository = Depends(get_event_repository),
):
    return await event_repo.list(
        tenant_id,
        device_id=device_id,
        severity=severity,
        event_type=event_type,
        since=since,
        limit=limit,
    )


@router.get("/collector-runs", response_model=list[CollectorRunOut])
async def list_collector_runs(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    monitoring_source_id: uuid.UUID | None = None,
    status: str | None = None,
    since: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    run_repo: CollectorRunRepository = Depends(get_collector_run_repository),
):
    return await run_repo.list(
        tenant_id,
        monitoring_source_id=monitoring_source_id,
        status=status,
        since=since,
        limit=limit,
    )


@router.get("/sensors/{sensor_id}", response_model=SensorDetailOut)
async def get_sensor(
    sensor_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    sensor_repo: SensorRepository = Depends(get_sensor_repository),
    device_repo: DeviceRepository = Depends(get_device_repository),
):
    """Top-level sensor lookup (mirrors how /devices/{device_id} works) -
    not nested under /devices/{device_id}/sensors/{sensor_id} since sensors
    aren't necessarily reachable through a single device path elsewhere."""
    sensor = await sensor_repo.get(sensor_id, tenant_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail=f"Sensor {sensor_id} not found")

    # Denormalized display field, set as a plain instance attribute (not a
    # mapped column) - mirrors AlertOut.device_name's pattern in
    # AlertRepository.list().
    device_name: str | None = None
    if sensor.device_id is not None:
        device = await device_repo.get(sensor.device_id, tenant_id)
        if device is not None:
            device_name = device.name
    sensor.device_name = device_name
    return sensor


@router.get("/sensors/{sensor_id}/metrics", response_model=list[MetricOut])
async def list_sensor_metrics(
    sensor_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    since: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    sensor_repo: SensorRepository = Depends(get_sensor_repository),
    metric_repo: MetricRepository = Depends(get_metric_repository),
):
    sensor = await sensor_repo.get(sensor_id, tenant_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail=f"Sensor {sensor_id} not found")
    return await metric_repo.list_for_sensor(sensor_id, since=since, limit=limit, offset=offset)


@router.get("/sensors/{sensor_id}/alerts", response_model=list[AlertOut])
async def list_sensor_alerts(
    sensor_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sensor_repo: SensorRepository = Depends(get_sensor_repository),
    alert_repo: AlertRepository = Depends(get_alert_repository),
):
    # AlertRepository.list()/count() gained an additive sensor_id filter
    # kwarg for this route (same pattern already used for device_id/site_id
    # in that same method) - see the repository's docstring/module notes.
    sensor = await sensor_repo.get(sensor_id, tenant_id)
    if sensor is None:
        raise HTTPException(status_code=404, detail=f"Sensor {sensor_id} not found")
    return await alert_repo.list(tenant_id, sensor_id=sensor_id, limit=limit, offset=offset)
