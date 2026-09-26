"""
Repository Pattern: all direct SQLAlchemy access for ServiceNowIncidentLink lives
here. CRUD + bidirectional lookup by correlation_id/sys_id/incident_id.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.servicenow import ServiceNowIncidentLink


class ServiceNowIncidentLinkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> ServiceNowIncidentLink:
        link = ServiceNowIncidentLink(**fields)
        self.db.add(link)
        await self.db.flush()
        await self.db.refresh(link)
        return link

    async def get(self, link_id: uuid.UUID, tenant_id: uuid.UUID) -> ServiceNowIncidentLink | None:
        stmt = select(ServiceNowIncidentLink).where(
            ServiceNowIncidentLink.id == link_id,
            ServiceNowIncidentLink.tenant_id == tenant_id,
            )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_incident_id(self, tenant_id: uuid.UUID, incident_id: uuid.UUID) -> ServiceNowIncidentLink | None:
        """Lookup the SN link for our local incident."""
        stmt = select(ServiceNowIncidentLink).where(
            ServiceNowIncidentLink.tenant_id == tenant_id,
            ServiceNowIncidentLink.incident_id == incident_id,
            )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_sn_sys_id(self, connection_id: uuid.UUID, sn_sys_id: str) -> ServiceNowIncidentLink | None:
        """For webhook inbound: look up by ServiceNow sys_id."""
        stmt = select(ServiceNowIncidentLink).where(
            ServiceNowIncidentLink.connection_id == connection_id,
            ServiceNowIncidentLink.sn_sys_id == sn_sys_id,
            )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_correlation_id(self, tenant_id: uuid.UUID, correlation_id: str) -> ServiceNowIncidentLink | None:
        """For webhook inbound: lookup by correlation_id WITHOUT knowing our internal incident UUID."""
        stmt = select(ServiceNowIncidentLink).where(
            ServiceNowIncidentLink.tenant_id == tenant_id,
            ServiceNowIncidentLink.correlation_id == correlation_id,
            )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        incident_id: uuid.UUID | None = None,
        connection_id: uuid.UUID | None = None,
        status_filter: str | None = None,
        limit: int = 100,
    ) -> list[ServiceNowIncidentLink]:
        """List links with optional filters."""
        stmt = select(ServiceNowIncidentLink).where(
            ServiceNowIncidentLink.tenant_id == tenant_id,
            )
        if incident_id is not None:
            stmt = stmt.where(ServiceNowIncidentLink.incident_id == incident_id)
        if connection_id is not None:
            stmt = stmt.where(ServiceNowIncidentLink.connection_id == connection_id)
        if status_filter is not None:
            stmt = stmt.where(ServiceNowIncidentLink.has_unresolved_conflict == (status_filter == "conflict"))
        stmt = stmt.order_by(ServiceNowIncidentLink.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update_hashes(
        self,
        link: ServiceNowIncidentLink,
        *,
        last_pushed_hash: str | None = None,
        last_pulled_hash: str | None = None,
        last_pushed_at=None,
        last_pulled_at=None,
    ) -> ServiceNowIncidentLink:
        if last_pushed_hash is not None:
            link.last_pushed_hash = last_pushed_hash
        if last_pulled_hash is not None:
            link.last_pulled_hash = last_pulled_hash
        if last_pushed_at is not None:
            link.last_pushed_at = last_pushed_at
        if last_pulled_at is not None:
            link.last_pulled_at = last_pulled_at
        await self.db.flush()
        await self.db.refresh(link)
        return link

    async def update_conflict(self, link: ServiceNowIncidentLink, *, has_conflict: bool, details: dict | None = None) -> ServiceNowIncidentLink:
        link.has_unresolved_conflict = has_conflict
        if details is not None:
            link.conflict_details = details
        await self.db.flush()
        await self.db.refresh(link)
        return link
