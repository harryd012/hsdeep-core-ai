"""
Repository Pattern: all direct SQLAlchemy access for NormalizedEvent lives here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_event import NormalizedEvent
from app.models.site import Site
from app.models.device import Device
from app.models.integration import Integration


class NormalizedEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- Writes --

    async def create(self, **fields) -> NormalizedEvent:
        event = NormalizedEvent(**fields)
        self.db.add(event)
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def update(self, event: NormalizedEvent, **fields) -> NormalizedEvent:
        for key, value in fields.items():
            if hasattr(event, key):
                setattr(event, key, value)
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def bump_occurrence(self, event: NormalizedEvent) -> NormalizedEvent:
        """Flapping: same signal re-triggered — bump count + timestamp."""
        event.occurrence_count += 1
        event.last_occurred_at = datetime.now(timezone.utc)
        await self.db.flush()
        return event

    async def suppress(self, event: NormalizedEvent) -> NormalizedEvent:
        """Mark event as suppressed (symptom of a root cause)."""
        event.is_suppressed = True
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def link_to_noc_event(
        self, event: NormalizedEvent, noc_event_id: uuid.UUID
    ) -> NormalizedEvent:
        """Link this normalized event to a NocEvent."""
        event.noc_event_id = noc_event_id
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def set_correlation_id(
        self, event: NormalizedEvent, correlation_id: uuid.UUID
    ) -> NormalizedEvent:
        """Stamp the correlation group id on this event."""
        event.correlation_id = correlation_id
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def list_uncorrelated(
        self,
        tenant_id: uuid.UUID,
        *,
        since: datetime,
        limit: int = 200,
    ) -> list[NormalizedEvent]:
        """Recent events not yet claimed by any correlation group.

        Uncorrelated = no correlation_id AND no noc_event_id. Ordered by
        event_timestamp ASC so the engine processes causes before symptoms.
        """
        stmt = (
            select(NormalizedEvent)
            .where(
                NormalizedEvent.tenant_id == tenant_id,
                NormalizedEvent.is_deleted.is_(False),
                NormalizedEvent.correlation_id.is_(None),
                NormalizedEvent.noc_event_id.is_(None),
                NormalizedEvent.event_timestamp >= since,
            )
            .order_by(NormalizedEvent.event_timestamp.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_noc_event(
        self, tenant_id: uuid.UUID, noc_event_id: uuid.UUID, limit: int = 500
    ) -> list[NormalizedEvent]:
        """All normalized events linked to one NocEvent (read-side)."""
        stmt = (
            select(NormalizedEvent)
            .where(
                NormalizedEvent.tenant_id == tenant_id,
                NormalizedEvent.is_deleted.is_(False),
                NormalizedEvent.noc_event_id == noc_event_id,
            )
            .order_by(NormalizedEvent.event_timestamp.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # -- Reads --

    async def get(self, event_id: uuid.UUID, tenant_id: uuid.UUID) -> NormalizedEvent | None:
        stmt = select(NormalizedEvent).where(
            NormalizedEvent.id == event_id,
            NormalizedEvent.tenant_id == tenant_id,
            NormalizedEvent.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_joins(
        self, event_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> NormalizedEvent | None:
        """Get event with site/device/integration names for display."""
        stmt = (
            select(
                NormalizedEvent,
                Site.name.label("site_name"),
                Device.name.label("device_name"),
                Integration.name.label("integration_name"),
            )
            .outerjoin(Site, Site.id == NormalizedEvent.site_id)
            .outerjoin(Device, Device.id == NormalizedEvent.device_id)
            .outerjoin(Integration, Integration.id == NormalizedEvent.integration_id)
            .where(
                NormalizedEvent.id == event_id,
                NormalizedEvent.tenant_id == tenant_id,
                NormalizedEvent.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        event, site_name, device_name, integration_name = row
        event.site_name = site_name
        event.device_name = device_name
        event.integration_name = integration_name
        return event

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        integration_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        event_category: str | None = None,
        event_type: str | None = None,
        telemetry_type: str | None = None,
        is_suppressed: bool | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NormalizedEvent]:
        stmt = select(NormalizedEvent).where(
            NormalizedEvent.tenant_id == tenant_id, NormalizedEvent.is_deleted.is_(False)
        )
        if integration_id:
            stmt = stmt.where(NormalizedEvent.integration_id == integration_id)
        if site_id:
            stmt = stmt.where(NormalizedEvent.site_id == site_id)
        if device_id:
            stmt = stmt.where(NormalizedEvent.device_id == device_id)
        if severity:
            stmt = stmt.where(NormalizedEvent.severity == severity)
        if event_category:
            stmt = stmt.where(NormalizedEvent.event_category == event_category)
        if event_type:
            stmt = stmt.where(NormalizedEvent.event_type == event_type)
        if telemetry_type:
            stmt = stmt.where(NormalizedEvent.telemetry_type == telemetry_type)
        if is_suppressed is not None:
            stmt = stmt.where(NormalizedEvent.is_suppressed == is_suppressed)
        if since:
            stmt = stmt.where(NormalizedEvent.event_timestamp >= since)
        if until:
            stmt = stmt.where(NormalizedEvent.event_timestamp <= until)
        stmt = stmt.order_by(NormalizedEvent.event_timestamp.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        integration_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        event_category: str | None = None,
        event_type: str | None = None,
        telemetry_type: str | None = None,
        is_suppressed: bool | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(NormalizedEvent).where(
            NormalizedEvent.tenant_id == tenant_id, NormalizedEvent.is_deleted.is_(False)
        )
        if integration_id:
            stmt = stmt.where(NormalizedEvent.integration_id == integration_id)
        if site_id:
            stmt = stmt.where(NormalizedEvent.site_id == site_id)
        if device_id:
            stmt = stmt.where(NormalizedEvent.device_id == device_id)
        if severity:
            stmt = stmt.where(NormalizedEvent.severity == severity)
        if event_category:
            stmt = stmt.where(NormalizedEvent.event_category == event_category)
        if event_type:
            stmt = stmt.where(NormalizedEvent.event_type == event_type)
        if telemetry_type:
            stmt = stmt.where(NormalizedEvent.telemetry_type == telemetry_type)
        if is_suppressed is not None:
            stmt = stmt.where(NormalizedEvent.is_suppressed == is_suppressed)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        total = await self.count(tenant_id)

        # By severity
        severity_stmt = (
            select(NormalizedEvent.severity, func.count())
            .where(
                NormalizedEvent.tenant_id == tenant_id, NormalizedEvent.is_deleted.is_(False)
            )
            .group_by(NormalizedEvent.severity)
        )
        severity_result = await self.db.execute(severity_stmt)
        by_severity = {sev: cnt for sev, cnt in severity_result.all()}

        # By category
        category_stmt = (
            select(NormalizedEvent.event_category, func.count())
            .where(
                NormalizedEvent.tenant_id == tenant_id, NormalizedEvent.is_deleted.is_(False)
            )
            .group_by(NormalizedEvent.event_category)
        )
        category_result = await self.db.execute(category_stmt)
        by_category = {cat: cnt for cat, cnt in category_result.all()}

        # By telemetry type
        telemetry_stmt = (
            select(NormalizedEvent.telemetry_type, func.count())
            .where(
                NormalizedEvent.tenant_id == tenant_id, NormalizedEvent.is_deleted.is_(False)
            )
            .group_by(NormalizedEvent.telemetry_type)
        )
        telemetry_result = await self.db.execute(telemetry_stmt)
        by_telemetry_type = {ttype: cnt for ttype, cnt in telemetry_result.all()}

        # Suppressed count
        suppressed = await self.count(tenant_id, is_suppressed=True)

        return {
            "total": total,
            "by_severity": by_severity,
            "by_category": by_category,
            "by_telemetry_type": by_telemetry_type,
            "suppressed_count": suppressed,
        }
