"""
Repository Pattern: all direct SQLAlchemy access for Runbook lives here,
same convention as SOPRepository. New repository for the SOP & Runbook
Intelligence Engine sprint.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runbook import Runbook


class RunbookRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> Runbook:
        runbook = Runbook(**fields)
        self.db.add(runbook)
        await self.db.flush()
        await self.db.refresh(runbook)
        return runbook

    async def get(self, runbook_id: uuid.UUID, tenant_id: uuid.UUID) -> Runbook | None:
        stmt = select(Runbook).where(
            Runbook.id == runbook_id, Runbook.tenant_id == tenant_id, Runbook.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_sop(self, sop_id: uuid.UUID, *, is_active: bool | None = True) -> list[Runbook]:
        stmt = select(Runbook).where(Runbook.sop_id == sop_id, Runbook.is_deleted.is_(False))
        if is_active is not None:
            stmt = stmt.where(Runbook.is_active == is_active)
        stmt = stmt.order_by(Runbook.name)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_sop_version(self, sop_version_id: uuid.UUID, *, is_active: bool | None = True) -> list[Runbook]:
        stmt = select(Runbook).where(Runbook.sop_version_id == sop_version_id, Runbook.is_deleted.is_(False))
        if is_active is not None:
            stmt = stmt.where(Runbook.is_active == is_active)
        stmt = stmt.order_by(Runbook.name)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        execution_mode: str | None = None,
        is_active: bool | None = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Runbook]:
        stmt = select(Runbook).where(Runbook.tenant_id == tenant_id, Runbook.is_deleted.is_(False))
        if execution_mode is not None:
            stmt = stmt.where(Runbook.execution_mode == execution_mode)
        if is_active is not None:
            stmt = stmt.where(Runbook.is_active == is_active)
        stmt = stmt.order_by(Runbook.name).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(self, tenant_id: uuid.UUID, *, is_active: bool | None = True) -> int:
        stmt = select(func.count()).select_from(Runbook).where(
            Runbook.tenant_id == tenant_id, Runbook.is_deleted.is_(False)
        )
        if is_active is not None:
            stmt = stmt.where(Runbook.is_active == is_active)
        result = await self.db.execute(stmt)
        return result.scalar_one()
