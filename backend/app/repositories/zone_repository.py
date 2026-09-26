"""Zone repository for database access."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.zone import Zone


class ZoneRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, tenant_id: uuid.UUID, site_id: uuid.UUID, name: str, code: str,
                     description: str | None = None, zone_type: str = "network") -> Zone:
        zone = Zone(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            site_id=site_id,
            name=name,
            code=code,
            description=description,
            zone_type=zone_type,
        )
        self.session.add(zone)
        await self.session.flush()
        return zone

    async def get(self, zone_id: uuid.UUID, tenant_id: uuid.UUID) -> Zone | None:
        result = await self.session.execute(
            select(Zone).where(Zone.id == zone_id, Zone.tenant_id == tenant_id, Zone.is_deleted.is_(False))
        )
        return result.scalar_one_or_none()

    async def get_with_devices(self, zone_id: uuid.UUID, tenant_id: uuid.UUID) -> Zone | None:
        result = await self.session.execute(
            select(Zone).where(Zone.id == zone_id, Zone.tenant_id == tenant_id, Zone.is_deleted.is_(False))
        )
        return result.scalar_one_or_none()

    async def list(self, tenant_id: uuid.UUID, *, site_id: uuid.UUID | None = None,
                   zone_type: str | None = None, limit: int = 100, offset: int = 0) -> list[Zone]:
        stmt = select(Zone).where(Zone.tenant_id == tenant_id, Zone.is_deleted.is_(False))
        if site_id:
            stmt = stmt.where(Zone.site_id == site_id)
        if zone_type:
            stmt = stmt.where(Zone.zone_type == zone_type)
        stmt = stmt.order_by(Zone.name).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, tenant_id: uuid.UUID, *, site_id: uuid.UUID | None = None,
                    zone_type: str | None = None) -> int:
        stmt = select(func.count()).select_from(Zone).where(Zone.tenant_id == tenant_id, Zone.is_deleted.is_(False))
        if site_id:
            stmt = stmt.where(Zone.site_id == site_id)
        if zone_type:
            stmt = stmt.where(Zone.zone_type == zone_type)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        total = await self.count(tenant_id)
        type_counts = {}
        for zone_type in ["network", "security", "cloud", "application"]:
            type_counts[zone_type] = await self.count(tenant_id, zone_type=zone_type)
        return {"total_zones": total, "by_type": type_counts}

    async def update(self, zone: Zone, **fields) -> Zone:
        for key, value in fields.items():
            setattr(zone, key, value)
        zone.updated_at = datetime.utcnow()
        await self.session.flush()
        return zone

    async def delete(self, zone: Zone) -> None:
        zone.is_deleted = True
        zone.deleted_at = datetime.utcnow()
        zone.updated_at = datetime.utcnow()
        await self.session.flush()
