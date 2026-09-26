"""
Health score calculation service.

Single source of truth for health scores across the platform. Every widget,
API endpoint, and report that shows a health score must call one of these
functions rather than compute its own — this prevents the Sprint 14-style
drift bug where different parts of the dashboard disagreed about what
"healthy" meant.

Scoring rules (device/sensor-centric, using the existing up|down|warning|
unknown vocabulary):
  - up -> 100
  - warning -> 60
  - down -> 0
  - unknown -> 50

Aggregation:
  - Device: weighted average of its sensors' health, or direct status
    mapping if no sensors exist.
  - Site: average of all device health scores at that site.
  - Tenant: average of all site health scores.
  - BusinessService: average of all device health scores mapped to that
    service.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_relationship import AssetRelationship
from app.models.device import Device
from app.models.sensor import Sensor
from app.models.site import Site
from app.models.business_service import BusinessService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_score(status: str) -> int:
    s = (status or "unknown").lower()
    if s == "up":
        return 100
    if s == "warning" or s == "warn":
        return 60
    if s == "down":
        return 0
    return 50


async def _get_device_sensor_health(db: AsyncSession, device_id: uuid.UUID) -> float:
    """Average sensor health for a single device."""
    stmt = select(
        func.avg(func.coalesce(func.nullif(Sensor.status, ""), "unknown"))
    ).where(Sensor.device_id == device_id, Sensor.is_deleted.is_(False))
    result = await db.execute(stmt)
    avg_status = result.scalar_one_or_none()
    if not avg_status:
        return 100.0
    return _status_score(avg_status)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def device_health(db: AsyncSession, device_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
    """Health score for a single device (0-100)."""
    device = (
        await db.execute(
            select(Device).where(Device.id == device_id, Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if not device:
        return 0

    sensor_avg = await _get_device_sensor_health(db, device.id)
    device_status_score = _status_score(device.status)
    # Blend: sensor average counts more if available, otherwise pure device status
    if sensor_avg > 0:
        return int((sensor_avg * 0.6) + (device_status_score * 0.4))
    return device_status_score


async def site_health(db: AsyncSession, site_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
    """Health score for a site (0-100)."""
    site = (
        await db.execute(
            select(Site).where(Site.id == site_id, Site.tenant_id == tenant_id, Site.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if not site:
        return 0

    devices = (
        await db.execute(
            select(Device).where(Device.site_id == site.id, Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
        )
    ).scalars().all()

    if not devices:
        return 100

    scores = [await _get_device_sensor_health(db, d.id) for d in devices]
    if not scores:
        return 100
    return int(sum(scores) / len(scores))


async def tenant_health(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Health score for an entire tenant (0-100)."""
    sites = (
        await db.execute(select(Site).where(Site.tenant_id == tenant_id, Site.is_deleted.is_(False)))
    ).scalars().all()

    if not sites:
        return 100

    scores = [await site_health(db, s.id, tenant_id) for s in sites]
    if not scores:
        return 100
    return int(sum(scores) / len(scores))


async def business_service_health(db: AsyncSession, service_id: uuid.UUID, tenant_id: uuid.UUID) -> int:
    """Health score for a business service (0-100), averaged across mapped devices."""
    service = (
        await db.execute(
            select(BusinessService).where(BusinessService.id == service_id, BusinessService.tenant_id == tenant_id, BusinessService.is_deleted.is_(False))
        )
    ).scalar_one_or_none()
    if not service:
        return 0

    devices = (
        await db.execute(
            select(Device).where(Device.business_service_id == service.id, Device.tenant_id == tenant_id, Device.is_deleted.is_(False))
        )
    ).scalars().all()

    if not devices:
        return 100

    scores = [await _get_device_sensor_health(db, d.id) for d in devices]
    if not scores:
        return 100
    return int(sum(scores) / len(scores))