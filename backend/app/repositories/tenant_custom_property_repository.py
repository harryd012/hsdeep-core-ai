"""Tenant-scoped repository for custom-property definitions (Phase 6.3E §3)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant_custom_property import TenantCustomProperty


class TenantCustomPropertyRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list(
        self, tenant_id: uuid.UUID, *, include_inactive: bool = True
    ) -> list[TenantCustomProperty]:
        stmt = select(TenantCustomProperty).where(
            TenantCustomProperty.tenant_id == tenant_id,
            TenantCustomProperty.is_deleted.is_(False),
        )
        if not include_inactive:
            stmt = stmt.where(TenantCustomProperty.is_active.is_(True))
        stmt = stmt.order_by(TenantCustomProperty.key)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get(
        self, property_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> TenantCustomProperty | None:
        """Always tenant-scoped: an id alone can never reach another tenant."""
        stmt = select(TenantCustomProperty).where(
            TenantCustomProperty.id == property_id,
            TenantCustomProperty.tenant_id == tenant_id,
            TenantCustomProperty.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_key(
        self, key: str, tenant_id: uuid.UUID
    ) -> TenantCustomProperty | None:
        stmt = select(TenantCustomProperty).where(
            TenantCustomProperty.tenant_id == tenant_id,
            TenantCustomProperty.key == key,
            TenantCustomProperty.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create(self, **fields) -> TenantCustomProperty:
        prop = TenantCustomProperty(**fields)
        self.db.add(prop)
        await self.db.flush()
        await self.db.refresh(prop)
        return prop

    async def update(
        self, prop: TenantCustomProperty, **fields
    ) -> TenantCustomProperty:
        for key, value in fields.items():
            setattr(prop, key, value)
        await self.db.flush()
        await self.db.refresh(prop)
        return prop

    async def soft_delete(self, prop: TenantCustomProperty) -> TenantCustomProperty:
        """Canonical project soft delete (see role_repository.soft_delete):
        flip `is_deleted` and stamp `deleted_at` so the row is retained for audit
        and for explaining historical tenant values."""
        prop.is_active = False
        prop.is_deleted = True
        prop.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
        return prop
