"""
Repository Pattern: all direct SQLAlchemy access for BusinessService lives
here, same convention as SiteRepository/DeviceRepository.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_service import BusinessService


class BusinessServiceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, business_service_id: uuid.UUID, tenant_id: uuid.UUID) -> BusinessService | None:
        stmt = select(BusinessService).where(
            BusinessService.id == business_service_id,
            BusinessService.tenant_id == tenant_id,
            BusinessService.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, tenant_id: uuid.UUID) -> list[BusinessService]:
        stmt = (
            select(BusinessService)
            .where(BusinessService.tenant_id == tenant_id, BusinessService.is_deleted.is_(False))
            .order_by(BusinessService.name)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(self, tenant_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(BusinessService).where(
            BusinessService.tenant_id == tenant_id, BusinessService.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()
