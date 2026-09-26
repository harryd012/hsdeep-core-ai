"""
Repository Pattern: all direct SQLAlchemy access for Integration lives here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Integration


class IntegrationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- Writes --

    async def create(self, **fields) -> Integration:
        integration = Integration(**fields)
        self.db.add(integration)
        await self.db.flush()
        await self.db.refresh(integration)
        return integration

    async def update(self, integration: Integration, **fields) -> Integration:
        for key, value in fields.items():
            if hasattr(integration, key):
                setattr(integration, key, value)
        await self.db.flush()
        await self.db.refresh(integration)
        return integration

    async def update_heartbeat(
        self, integration_id: uuid.UUID, success: bool, error: str | None = None
    ) -> Integration | None:
        integration = await self.get(integration_id)
        if integration is None:
            return None
        now = datetime.now(timezone.utc)
        if success:
            integration.last_successful_collection = now
            integration.status = "connected"
            integration.last_error = None
        else:
            integration.status = "degraded"
            integration.last_error = error
            integration.last_error_at = now
        await self.db.flush()
        await self.db.refresh(integration)
        return integration

    # -- Reads --

    async def get(self, integration_id: uuid.UUID) -> Integration | None:
        stmt = select(Integration).where(
            Integration.id == integration_id, Integration.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_tenant(
        self, integration_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Integration | None:
        stmt = select(Integration).where(
            Integration.id == integration_id,
            Integration.tenant_id == tenant_id,
            Integration.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_type: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Integration]:
        stmt = select(Integration).where(
            Integration.tenant_id == tenant_id, Integration.is_deleted.is_(False)
        )
        if provider_type:
            stmt = stmt.where(Integration.provider_type == provider_type)
        if provider:
            stmt = stmt.where(Integration.provider == provider)
        if status:
            stmt = stmt.where(Integration.status == status)
        stmt = stmt.order_by(Integration.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_type: str | None = None,
        provider: str | None = None,
        status: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Integration).where(
            Integration.tenant_id == tenant_id, Integration.is_deleted.is_(False)
        )
        if provider_type:
            stmt = stmt.where(Integration.provider_type == provider_type)
        if provider:
            stmt = stmt.where(Integration.provider == provider)
        if status:
            stmt = stmt.where(Integration.status == status)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        total = await self.count(tenant_id)
        connected = await self.count(tenant_id, status="connected")
        degraded = await self.count(tenant_id, status="degraded")
        disconnected = await self.count(tenant_id, status="disconnected")
        disabled = await self.count(tenant_id, status="disabled")

        # Total events ingested across all integrations
        stmt = select(func.coalesce(func.sum(Integration.total_events_ingested), 0)).where(
            Integration.tenant_id == tenant_id, Integration.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        total_events = result.scalar_one()

        return {
            "total": total,
            "connected": connected,
            "degraded": degraded,
            "disconnected": disconnected,
            "disabled": disabled,
            "total_events_ingested": total_events,
        }
