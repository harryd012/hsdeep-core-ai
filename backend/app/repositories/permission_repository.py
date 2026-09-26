"""
Repository Pattern: direct SQLAlchemy access for the system-wide Permission
catalog (Auth + RBAC sprint, P1). Permissions are not tenant-scoped
(app/models/permission.py), so no tenant filter here.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.permission import Permission


class PermissionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_all(self) -> list[Permission]:
        result = await self.db.execute(select(Permission).order_by(Permission.code))
        return list(result.scalars().all())

    async def get_by_codes(self, codes: Iterable[str]) -> list[Permission]:
        codes = list(codes)
        if not codes:
            return []
        result = await self.db.execute(select(Permission).where(Permission.code.in_(codes)))
        return list(result.scalars().all())
