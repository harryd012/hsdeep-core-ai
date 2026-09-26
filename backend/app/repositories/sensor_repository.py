from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sensor import Sensor


class SensorRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_external_id(
        self, monitoring_source_id: uuid.UUID, external_id: str
    ) -> Sensor | None:
        stmt = select(Sensor).where(
            Sensor.monitoring_source_id == monitoring_source_id,
            Sensor.external_id == external_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        external_id: str,
        name: str,
        metric_type: str,
        unit: str | None,
        current_value: float | None,
        status: str,
        warning_threshold: float | None = None,
        critical_threshold: float | None = None,
        interface_id: uuid.UUID | None = None,
    ) -> tuple[Sensor, bool]:
        """Create or refresh a sensor from an adapter reading.

        Returns (sensor, created) so the sync service can report accurate
        created/updated counts back to the caller.
        """
        existing = await self.get_by_external_id(monitoring_source_id, external_id)
        now = datetime.now(timezone.utc)

        if existing is None:
            sensor = Sensor(
                tenant_id=tenant_id,
                monitoring_source_id=monitoring_source_id,
                external_id=external_id,
                name=name,
                metric_type=metric_type,
                unit=unit,
                current_value=current_value,
                status=status,
                warning_threshold=warning_threshold,
                critical_threshold=critical_threshold,
                interface_id=interface_id,
                last_value_at=now,
            )
            self.db.add(sensor)
            await self.db.flush()
            await self.db.refresh(sensor)
            return sensor, True

        existing.name = name
        existing.metric_type = metric_type
        existing.unit = unit
        existing.current_value = current_value
        existing.status = status
        if warning_threshold is not None:
            existing.warning_threshold = warning_threshold
        if critical_threshold is not None:
            existing.critical_threshold = critical_threshold
        if interface_id is not None:
            existing.interface_id = interface_id
        existing.last_value_at = now
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def get(self, sensor_id: uuid.UUID, tenant_id: uuid.UUID) -> Sensor | None:
        stmt = select(Sensor).where(
            Sensor.id == sensor_id,
            Sensor.tenant_id == tenant_id,
            Sensor.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_device(self, device_id: uuid.UUID) -> list[Sensor]:
        stmt = (
            select(Sensor)
            .where(Sensor.device_id == device_id, Sensor.is_deleted.is_(False))
            .order_by(Sensor.name)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_source(self, monitoring_source_id: uuid.UUID) -> list[Sensor]:
        stmt = (
            select(Sensor)
            .where(Sensor.monitoring_source_id == monitoring_source_id, Sensor.is_deleted.is_(False))
            .order_by(Sensor.name)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        status: str | None = None,
        metric_type: str | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Sensor]:
        stmt = select(Sensor).where(Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False))
        if device_id is not None:
            stmt = stmt.where(Sensor.device_id == device_id)
        if status is not None:
            stmt = stmt.where(Sensor.status == status)
        if metric_type is not None:
            stmt = stmt.where(Sensor.metric_type == metric_type)
        if site_id is not None:
            # sensors don't carry site_id directly - join through device
            from app.models.device import Device

            stmt = stmt.join(Device, Device.id == Sensor.device_id).where(Device.site_id == site_id)
        if search:
            stmt = stmt.where(Sensor.name.ilike(f"%{search}%"))
        stmt = stmt.order_by(Sensor.name)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_for_tenant(
        self,
        tenant_id: uuid.UUID,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        status: str | None = None,
        metric_type: str | None = None,
        search: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Sensor).where(
            Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False)
        )
        if device_id is not None:
            stmt = stmt.where(Sensor.device_id == device_id)
        if status is not None:
            stmt = stmt.where(Sensor.status == status)
        if metric_type is not None:
            stmt = stmt.where(Sensor.metric_type == metric_type)
        if site_id is not None:
            from app.models.device import Device

            stmt = stmt.join(Device, Device.id == Sensor.device_id).where(Device.site_id == site_id)
        if search:
            stmt = stmt.where(Sensor.name.ilike(f"%{search}%"))
        result = await self.db.execute(stmt)
        return result.scalar_one()
