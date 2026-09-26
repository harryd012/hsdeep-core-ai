"""
Repository Pattern: all direct SQLAlchemy access for MaintenanceWindow
lives here, same convention as SiteRepository/DeviceRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.maintenance_window import MaintenanceWindow


class MaintenanceWindowRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> MaintenanceWindow:
        window = MaintenanceWindow(**fields)
        self.db.add(window)
        await self.db.flush()
        await self.db.refresh(window)
        return window

    async def list_active_for_scope(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None,
        device_id: uuid.UUID | None,
        at: datetime,
    ) -> list[MaintenanceWindow]:
        """Windows covering `at` that apply to the given site/device scope -
        tenant-wide (site_id and device_id both NULL), this specific site
        (site_id matches, device_id NULL), or this specific device
        (device_id matches). Used by PriorityEngine's maintenance-window
        factor to check "is this incident's device/site under planned
        maintenance right now"."""
        stmt = select(MaintenanceWindow).where(
            MaintenanceWindow.tenant_id == tenant_id,
            MaintenanceWindow.starts_at <= at,
            MaintenanceWindow.ends_at >= at,
        )
        if device_id is not None:
            stmt = stmt.where(
                (MaintenanceWindow.device_id == device_id)
                | ((MaintenanceWindow.device_id.is_(None)) & (MaintenanceWindow.site_id == site_id))
                | ((MaintenanceWindow.device_id.is_(None)) & (MaintenanceWindow.site_id.is_(None)))
            )
        elif site_id is not None:
            stmt = stmt.where(
                (MaintenanceWindow.site_id == site_id) & (MaintenanceWindow.device_id.is_(None))
                | (MaintenanceWindow.site_id.is_(None) & MaintenanceWindow.device_id.is_(None))
            )
        else:
            stmt = stmt.where(MaintenanceWindow.site_id.is_(None), MaintenanceWindow.device_id.is_(None))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list(self, tenant_id: uuid.UUID, *, limit: int = 100, offset: int = 0) -> list[MaintenanceWindow]:
        stmt = (
            select(MaintenanceWindow)
            .where(MaintenanceWindow.tenant_id == tenant_id)
            .order_by(MaintenanceWindow.starts_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
