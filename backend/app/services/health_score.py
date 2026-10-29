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
  - unknown (or any other value) -> no score

A score of ``None`` means "no evidence", never "healthy": a device with no
known sensor or device status, a site with no scored devices and an empty
tenant all return ``None``. Missing data must not read as 100%.

Only fresh evidence counts. A device's own status is used only when its
``last_seen_at`` is within ``DEVICE_FRESHNESS_WINDOW_SECONDS`` (the window
the canonical up/down/stale/unknown classification uses), and a sensor's
status only when its ``last_value_at`` is. An "up" reading from last week is
not evidence of health today.

Aggregation:
  - Device: 60/40 blend of its known sensors' average and its own status;
    whichever side is known when only one is.
  - Site / BusinessService / Tenant: average of the known scores of the
    devices in scope (the tenant scope covers every device, including ones
    not assigned to a site). Devices without a score are left out.

Lookups are tenant-scoped; a scope id from another tenant (or a missing
one) raises ``HealthScopeNotFound`` so routes can answer 404.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_service import BusinessService
from app.models.device import Device
from app.models.sensor import Sensor
from app.models.site import Site
from app.services.operational_intelligence_service import DEVICE_FRESHNESS_WINDOW_SECONDS

_STATUS_SCORES: dict[str, int] = {"up": 100, "warning": 60, "warn": 60, "down": 0}
_SENSOR_WEIGHT = 0.6


class HealthScopeNotFound(LookupError):
    """The device / site / business service does not exist in this tenant."""


@dataclass(frozen=True)
class HealthRollup:
    """A score plus how much evidence it rests on."""

    score: int | None
    scored: int  # devices that contributed a score
    total: int  # devices in scope

    @property
    def status(self) -> str:
        return health_status(self.score)


def health_status(score: int | None) -> str:
    """healthy | degraded | down | unknown - the label shown beside a score."""
    if score is None:
        return "unknown"
    if score >= 80:
        return "healthy"
    if score >= 50:
        return "degraded"
    return "down"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_score(status: str | None) -> int | None:
    return _STATUS_SCORES.get((status or "").strip().lower())


def _fresh(observed_at: datetime | None, now: datetime) -> bool:
    if observed_at is None:
        return False
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return now - observed_at <= timedelta(seconds=DEVICE_FRESHNESS_WINDOW_SECONDS)


def _device_score(device_status: str | None, sensor_statuses: list[str]) -> int | None:
    """Score from statuses that are already known to be fresh."""
    sensor_scores = [s for s in (_status_score(v) for v in sensor_statuses) if s is not None]
    own = _status_score(device_status)
    if sensor_scores:
        sensor_avg = sum(sensor_scores) / len(sensor_scores)
        if own is None:
            return round(sensor_avg)
        return round(sensor_avg * _SENSOR_WEIGHT + own * (1 - _SENSOR_WEIGHT))
    return own


async def _sensor_statuses(
    db: AsyncSession, tenant_id: uuid.UUID, device_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    if not device_ids:
        return {}
    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(Sensor.device_id, Sensor.status, Sensor.last_value_at).where(
            Sensor.tenant_id == tenant_id,
            Sensor.device_id.in_(device_ids),
            Sensor.is_deleted.is_(False),
        )
    )
    statuses: dict[uuid.UUID, list[str]] = defaultdict(list)
    for device_id, status, last_value_at in rows.all():
        if _fresh(last_value_at, now):
            statuses[device_id].append(status)
    return statuses


async def _rollup(db: AsyncSession, tenant_id: uuid.UUID, *conditions) -> HealthRollup:
    devices = (
        await db.execute(
            select(Device.id, Device.status, Device.last_seen_at).where(
                Device.tenant_id == tenant_id, Device.is_deleted.is_(False), *conditions
            )
        )
    ).all()
    sensors = await _sensor_statuses(db, tenant_id, [d.id for d in devices])
    now = datetime.now(timezone.utc)
    scores = [
        s
        for s in (
            _device_score(d.status if _fresh(d.last_seen_at, now) else None, sensors.get(d.id, []))
            for d in devices
        )
        if s is not None
    ]
    score = round(sum(scores) / len(scores)) if scores else None
    return HealthRollup(score=score, scored=len(scores), total=len(devices))


async def _require(db: AsyncSession, model, scope_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
    found = (
        await db.execute(
            select(model.id).where(
                model.id == scope_id, model.tenant_id == tenant_id, model.is_deleted.is_(False)
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise HealthScopeNotFound(str(scope_id))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def device_health(db: AsyncSession, device_id: uuid.UUID, tenant_id: uuid.UUID) -> int | None:
    """Health score for a single device (0-100), or None without evidence."""
    await _require(db, Device, device_id, tenant_id)
    return (await _rollup(db, tenant_id, Device.id == device_id)).score


async def site_health_rollup(db: AsyncSession, site_id: uuid.UUID, tenant_id: uuid.UUID) -> HealthRollup:
    await _require(db, Site, site_id, tenant_id)
    return await _rollup(db, tenant_id, Device.site_id == site_id)


async def site_health(db: AsyncSession, site_id: uuid.UUID, tenant_id: uuid.UUID) -> int | None:
    """Health score for a site (0-100), or None without evidence."""
    return (await site_health_rollup(db, site_id, tenant_id)).score


async def tenant_health_rollup(db: AsyncSession, tenant_id: uuid.UUID) -> HealthRollup:
    return await _rollup(db, tenant_id)


async def tenant_health(db: AsyncSession, tenant_id: uuid.UUID) -> int | None:
    """Health score for an entire tenant (0-100), or None without evidence."""
    return (await tenant_health_rollup(db, tenant_id)).score


async def business_service_health_rollup(
    db: AsyncSession, service_id: uuid.UUID, tenant_id: uuid.UUID
) -> HealthRollup:
    await _require(db, BusinessService, service_id, tenant_id)
    return await _rollup(db, tenant_id, Device.business_service_id == service_id)


async def business_service_health(
    db: AsyncSession, service_id: uuid.UUID, tenant_id: uuid.UUID
) -> int | None:
    """Health score for a business service (0-100), or None without evidence."""
    return (await business_service_health_rollup(db, service_id, tenant_id)).score
