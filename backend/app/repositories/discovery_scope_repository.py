"""DiscoveryScope repository. Every query is tenant-scoped (Phase 6.3 §4)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discovery_scope import DiscoveryScope


class DiscoveryScopeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list(self, tenant_id: uuid.UUID) -> list[DiscoveryScope]:
        """Scopes for ONE tenant. Never optional, never inferred from a target."""
        stmt = (
            select(DiscoveryScope)
            .where(
                DiscoveryScope.tenant_id == tenant_id,
                DiscoveryScope.is_deleted.is_(False),
            )
            .order_by(DiscoveryScope.name)
        )
        return list((await self.db.execute(stmt)).scalars().all())

    async def get(
        self, scope_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> DiscoveryScope | None:
        stmt = select(DiscoveryScope).where(
            DiscoveryScope.id == scope_id,
            DiscoveryScope.tenant_id == tenant_id,
            DiscoveryScope.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_by_name(
        self, name: str, tenant_id: uuid.UUID
    ) -> DiscoveryScope | None:
        stmt = select(DiscoveryScope).where(
            DiscoveryScope.name == name,
            DiscoveryScope.tenant_id == tenant_id,
            DiscoveryScope.is_deleted.is_(False),
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def create(self, **fields) -> DiscoveryScope:
        scope = DiscoveryScope(**fields)
        self.db.add(scope)
        await self.db.flush()
        await self.db.refresh(scope)
        return scope

    async def update(self, scope: DiscoveryScope, **fields) -> DiscoveryScope:
        for key, value in fields.items():
            setattr(scope, key, value)
        await self.db.flush()
        await self.db.refresh(scope)
        return scope

    async def delete(self, scope: DiscoveryScope) -> None:
        """Soft-delete, matching the project's SoftDeleteMixin convention."""
        scope.is_deleted = True
        await self.db.flush()
