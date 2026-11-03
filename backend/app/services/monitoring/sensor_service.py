"""
Sensor detail, channel history and lifecycle (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P1).

Tenant-scoped reads over `sensors` and the append-only `metrics` history, and
the user's changes to a sensor (pause / disable / resume, description, own
thresholds). A change takes effect at once: the sensor's status is recomputed
with `sensor_policy` and passed to the Alert Engine, so pausing resolves an
open alert and a tighter threshold can open one, exactly as the next sync would.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.models.interface import Interface
from app.models.metric import Metric
from app.models.monitoring_source import MonitoringSource
from app.models.sensor import Sensor
from app.schemas.monitoring import (
    SensorChannelOut,
    SensorDetailOut,
    SensorHistoryOut,
    SensorHistoryPoint,
    SensorOpenAlert,
    SensorRef,
    SensorUpdate,
)
from app.services.monitoring.sensor_policy import PRIMARY_CHANNEL, UserThresholds, effective_status, user_thresholds_of
MAX_POINTS = 300
MIN_BUCKET_SECONDS = 60


def channel_key():
    """SQL expression: the metric's channel (dimensions.channel), else its metric type."""
    return func.coalesce(Metric.dimensions["channel"].astext, Metric.metric_type)


def bucket_seconds(hours: int, points: int = MAX_POINTS) -> int:
    """Bucket width that keeps a window of `hours` within `points` buckets."""
    return max(MIN_BUCKET_SECONDS, math.ceil(hours * 3600 / max(1, points)))


class SensorNotFound(Exception):
    pass


class SensorService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _get(self, tenant_id: uuid.UUID, sensor_id: uuid.UUID) -> Sensor:
        sensor = (
            await self.db.execute(
                select(Sensor).where(
                    Sensor.id == sensor_id, Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False)
                )
            )
        ).scalar_one_or_none()
        if sensor is None:
            raise SensorNotFound()
        return sensor

    async def detail(self, tenant_id: uuid.UUID, sensor_id: uuid.UUID, now: datetime | None = None) -> SensorDetailOut:
        now = now or datetime.now(timezone.utc)
        sensor = await self._get(tenant_id, sensor_id)
        source = (await self.db.execute(select(MonitoringSource).where(MonitoringSource.id == sensor.monitoring_source_id))).scalar_one()
        device = (
            await self.db.execute(select(Device.id, Device.name).where(Device.id == sensor.device_id, Device.tenant_id == tenant_id))
        ).first() if sensor.device_id else None
        interface = (
            await self.db.execute(select(Interface.id, Interface.name).where(Interface.id == sensor.interface_id))
        ).first() if sensor.interface_id else None

        key = channel_key()
        stats = (
            await self.db.execute(
                select(
                    key,
                    func.max(Metric.recorded_at),
                    func.count().filter(Metric.recorded_at >= now - timedelta(hours=24)),
                )
                .where(Metric.tenant_id == tenant_id, Metric.sensor_id == sensor.id)
                .group_by(key)
            )
        ).all()
        channels = []
        for name, last_at, samples in stats:
            last = (
                await self.db.execute(
                    select(Metric.value, Metric.unit)
                    .where(Metric.tenant_id == tenant_id, Metric.sensor_id == sensor.id, key == name, Metric.recorded_at == last_at)
                    .limit(1)
                )
            ).first()
            channels.append(
                SensorChannelOut(
                    key=name, unit=last.unit if last else None, last_value=last.value if last else None,
                    last_recorded_at=last_at, samples_24h=samples or 0,
                )
            )
        channels.sort(key=lambda c: (c.key != PRIMARY_CHANNEL, c.key.lower()))

        alert = (
            await self.db.execute(
                select(Alert)
                .where(Alert.tenant_id == tenant_id, Alert.sensor_id == sensor.id, Alert.status != "resolved", Alert.is_deleted.is_(False))
                .order_by(Alert.last_occurred_at.desc().nulls_last())
                .limit(1)
            )
        ).scalar_one_or_none()

        return SensorDetailOut(
            id=sensor.id, tenant_id=sensor.tenant_id, monitoring_source_id=sensor.monitoring_source_id,
            device_id=sensor.device_id, external_id=sensor.external_id, name=sensor.name,
            metric_type=sensor.metric_type, unit=sensor.unit, current_value=sensor.current_value,
            warning_threshold=sensor.warning_threshold, critical_threshold=sensor.critical_threshold,
            status=sensor.status, last_value_at=sensor.last_value_at, created_at=sensor.created_at,
            updated_at=sensor.updated_at, admin_state=sensor.admin_state, threshold_source=sensor.threshold_source,
            description=sensor.description, threshold_direction=sensor.threshold_direction,
            device=SensorRef(id=device.id, name=device.name) if device else None,
            interface=SensorRef(id=interface.id, name=interface.name) if interface else None,
            source=SensorRef(id=source.id, name=source.name), source_type=source.source_type,
            channels=channels,
            open_alert=SensorOpenAlert(id=alert.id, title=alert.title, severity=alert.severity, status=alert.status) if alert else None,
        )

    async def history(
        self, tenant_id: uuid.UUID, sensor_id: uuid.UUID, *, channel: str = PRIMARY_CHANNEL, hours: int = 24,
        now: datetime | None = None,
    ) -> SensorHistoryOut:
        """One channel bucketed over the window: avg / min / max / count per bucket
        (computed in the database; empty buckets are not returned)."""
        now = now or datetime.now(timezone.utc)
        sensor = await self._get(tenant_id, sensor_id)
        width = bucket_seconds(hours)
        bucket = func.to_timestamp(func.floor(func.extract("epoch", Metric.recorded_at) / width) * width)
        rows = (
            await self.db.execute(
                select(bucket, func.avg(Metric.value), func.min(Metric.value), func.max(Metric.value), func.count(Metric.value))
                .where(
                    Metric.tenant_id == tenant_id,
                    Metric.sensor_id == sensor.id,
                    channel_key() == channel,
                    Metric.recorded_at >= now - timedelta(hours=hours),
                    Metric.value.is_not(None),
                )
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()
        unit = (
            await self.db.execute(
                select(Metric.unit)
                .where(Metric.tenant_id == tenant_id, Metric.sensor_id == sensor.id, channel_key() == channel)
                .order_by(Metric.recorded_at.desc())
                .limit(1)
            )
        ).scalar()
        return SensorHistoryOut(
            channel=channel, unit=unit, hours=hours, bucket_seconds=width,
            points=[
                SensorHistoryPoint(t=t, avg=float(a) if a is not None else None, min=lo, max=hi, count=n)
                for t, a, lo, hi, n in rows
            ],
        )

    async def update(self, tenant_id: uuid.UUID, sensor_id: uuid.UUID, change: SensorUpdate) -> tuple[Sensor, dict, dict]:
        """Apply the change; returns (sensor, old values, new values) of what changed."""
        sensor = await self._get(tenant_id, sensor_id)
        old: dict = {}
        new: dict = {}

        def put(field: str, value) -> None:
            if getattr(sensor, field) != value:
                old[field] = getattr(sensor, field)
                new[field] = value
                setattr(sensor, field, value)

        if change.admin_state is not None:
            put("admin_state", change.admin_state)
        if change.description is not None:
            put("description", change.description.strip() or None)
        if change.thresholds is not None:
            put("threshold_source", "user")
            put("warning_threshold", change.thresholds.warning)
            put("critical_threshold", change.thresholds.critical)
            put("threshold_direction", change.thresholds.direction)
        elif change.reset_thresholds:
            put("threshold_source", "provider")
            put("threshold_direction", None)

        if new:
            if sensor.admin_state == "active" and old.get("admin_state") in ("paused", "disabled"):
                # Resumed: the collector decides the status from its next reading.
                collector_status = "unknown"
            else:
                collector_status = sensor.status if sensor.status not in ("paused", "disabled") else "unknown"
            thresholds: UserThresholds | None = user_thresholds_of(sensor)
            put("status", effective_status(collector_status, sensor.current_value, sensor.admin_state, thresholds))
            await self.db.flush()
        return sensor, old, new


__all__ = ["PRIMARY_CHANNEL", "SensorNotFound", "SensorService", "bucket_seconds", "channel_key"]
