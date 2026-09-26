"""Zone service for business logic."""
from __future__ import annotations

import uuid

from app.models.zone import Zone
from app.repositories.zone_repository import ZoneRepository


class ZoneNotFound(Exception):
    pass


class ZoneService:
    def __init__(self, repo: ZoneRepository):
        self.repo = repo

    async def create(self, *, tenant_id: uuid.UUID, site_id: uuid.UUID, name: str, code: str,
                     description: str | None = None, zone_type: str = "network") -> Zone:
        return await self.repo.create(
            tenant_id=tenant_id,
            site_id=site_id,
            name=name,
            code=code,
            description=description,
            zone_type=zone_type,
        )

    async def get(self, zone_id: uuid.UUID, tenant_id: uuid.UUID) -> Zone:
        zone = await self.repo.get(zone_id, tenant_id)
        if zone is None:
            raise ZoneNotFound(f"Zone {zone_id} not found")
        return zone

    async def list(self, tenant_id: uuid.UUID, *, site_id: uuid.UUID | None = None,
                   zone_type: str | None = None, limit: int = 100, offset: int = 0) -> list[Zone]:
        return await self.repo.list(tenant_id, site_id=site_id, zone_type=zone_type, limit=limit, offset=offset)

    async def count(self, tenant_id: uuid.UUID, *, site_id: uuid.UUID | None = None,
                    zone_type: str | None = None) -> int:
        return await self.repo.count(tenant_id, site_id=site_id, zone_type=zone_type)

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        return await self.repo.summary(tenant_id)

    async def update(self, zone_id: uuid.UUID, tenant_id: uuid.UUID, *, name: str | None = None,
                     description: str | None = None, zone_type: str | None = None) -> Zone:
        zone = await self.get(zone_id, tenant_id)
        fields = {}
        if name is not None:
            fields["name"] = name
        if description is not None:
            fields["description"] = description
        if zone_type is not None:
            fields["zone_type"] = zone_type
        if fields:
            return await self.repo.update(zone, **fields)
        return zone

    async def delete(self, zone_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        zone = await self.get(zone_id, tenant_id)
        await self.repo.delete(zone)
