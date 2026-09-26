"""
Repository Pattern: all direct SQLAlchemy access for Site lives here. The
service layer talks to this class only - it never issues its own queries.

Deliberately minimal: only what the Dashboard services (task 20.x) need
today - a tenant-scoped count and a basic list for online/offline
aggregation. Extend narrowly if a future task needs more (e.g. a
site_hint-by-name/code lookup for BundleSaver's device identity resolution -
tracked as a known gap in bundle_saver.py, not solved here).
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.site import Site


class SiteRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, site_id: uuid.UUID, tenant_id: uuid.UUID) -> Site | None:
        stmt = select(Site).where(
            Site.id == site_id,
            Site.tenant_id == tenant_id,
            Site.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def count(self, tenant_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Site).where(
            Site.tenant_id == tenant_id,
            Site.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def list(self, tenant_id: uuid.UUID) -> list[Site]:
        stmt = select(Site).where(
            Site.tenant_id == tenant_id,
            Site.is_deleted.is_(False),
        ).order_by(Site.name)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
