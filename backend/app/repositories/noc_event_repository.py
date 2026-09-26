"""
Repository Pattern: all direct SQLAlchemy access for NocEvent lives here.
The service layer talks to this class only, same convention as
AlertRepository / IncidentRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.noc_event import NocEvent
from app.models.site import Site
from app.models.device import Device

_ALL_SEVERITIES = ("critical", "warning", "info")
_ALL_STATUSES = ("open", "acknowledged", "resolved")
_ALL_CATEGORIES = (
    "availability", "performance", "capacity", "connectivity",
    "configuration", "security", "other",
)


class NocEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -- Writes --

    async def create(self, **fields) -> NocEvent:
        event = NocEvent(**fields)
        self.db.add(event)
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def update(self, event: NocEvent, **fields) -> NocEvent:
        for key, value in fields.items():
            if hasattr(event, key):
                setattr(event, key, value)
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def acknowledge(
        self,
        event: NocEvent,
        *,
        acknowledged_by: uuid.UUID | None = None,
    ) -> NocEvent:
        now = datetime.now(timezone.utc)
        event.status = "acknowledged"
        event.acknowledged_at = now
        event.acknowledged_by = acknowledged_by
        event.last_occurred_at = now
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def resolve(
        self,
        event: NocEvent,
        *,
        resolved_by: uuid.UUID | None = None,
    ) -> NocEvent:
        now = datetime.now(timezone.utc)
        event.status = "resolved"
        event.resolved_at = now
        event.resolved_by = resolved_by
        event.last_occurred_at = now
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def assign_analyst(
        self, event: NocEvent, analyst_id: uuid.UUID
    ) -> NocEvent:
        event.assigned_analyst_id = analyst_id
        if event.status == "open":
            event.status = "acknowledged"
            event.acknowledged_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(event)
        return event

    async def bump_occurrence(self, event: NocEvent) -> NocEvent:
        """Flapping: same signal re-triggered — bump count + timestamp."""
        event.occurrence_count += 1
        event.last_occurred_at = datetime.now(timezone.utc)
        await self.db.flush()
        return event

    async def find_active_by_signature(
        self, tenant_id: uuid.UUID, source: str, event_signature: str
    ) -> NocEvent | None:
        """Find the single open (non-resolved) NOC event for a source+signature.

        Used by correlation to reuse an existing operational event instead of
        creating a duplicate on every correlation run (idempotency).
        """
        stmt = (
            select(NocEvent)
            .where(
                NocEvent.tenant_id == tenant_id,
                NocEvent.source == source,
                NocEvent.event_signature == event_signature,
                NocEvent.status != "resolved",
                NocEvent.is_deleted.is_(False),
            )
            .order_by(NocEvent.triggered_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # -- Reads --

    async def get(self, event_id: uuid.UUID, tenant_id: uuid.UUID) -> NocEvent | None:
        stmt = select(NocEvent).where(
            NocEvent.id == event_id,
            NocEvent.tenant_id == tenant_id,
            NocEvent.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_with_joins(
        self, event_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> NocEvent | None:
        """Get event with site/device/analyst denormalized names loaded."""
        stmt = (
            select(NocEvent)
            .outerjoin(Site, Site.id == NocEvent.site_id)
            .outerjoin(Device, Device.id == NocEvent.device_id)
            .where(
                NocEvent.id == event_id,
                NocEvent.tenant_id == tenant_id,
                NocEvent.is_deleted.is_(False),
            )
            # Eager-load: names below are read from relationships, which must
            # not lazy-load inside an async session (MissingGreenlet).
            .options(
                selectinload(NocEvent.site),
                selectinload(NocEvent.device),
                selectinload(NocEvent.assigned_analyst),
                selectinload(NocEvent.source_agent),
            )
        )
        result = await self.db.execute(stmt)
        event = result.scalar_one_or_none()
        if event is not None:
            event.site_name = event.site.name if event.site else None
            event.device_name = event.device.name if event.device else None
            if event.assigned_analyst is not None:
                event.assigned_analyst_name = event.assigned_analyst.name
            if event.source_agent is not None:
                event.source_agent_name = event.source_agent.name
        return event

    async def get_active_for_signature(
        self,
        tenant_id: uuid.UUID,
        source: str,
        event_signature: str,
    ) -> NocEvent | None:
        """Find the existing non-resolved event for a source+signature pair —
        used for flapping dedup (same signal = bump, not create)."""
        stmt = select(NocEvent).where(
            NocEvent.tenant_id == tenant_id,
            NocEvent.source == source,
            NocEvent.event_signature == event_signature,
            NocEvent.status != "resolved",
            NocEvent.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        category: str | None = None,
        source: str | None = None,
        assigned_analyst_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NocEvent]:
        stmt = (
            select(NocEvent)
            .where(
                NocEvent.tenant_id == tenant_id,
                NocEvent.is_deleted.is_(False),
            )
            # Eager-load display relationships: these are accessed after the
            # query below, and lazy-loading them in an async session raises
            # MissingGreenlet on a fresh (request-scoped) session.
            .options(
                selectinload(NocEvent.site),
                selectinload(NocEvent.device),
                selectinload(NocEvent.assigned_analyst),
            )
        )
        if site_id is not None:
            stmt = stmt.where(NocEvent.site_id == site_id)
        if device_id is not None:
            stmt = stmt.where(NocEvent.device_id == device_id)
        if severity is not None:
            stmt = stmt.where(NocEvent.severity == severity)
        if status is not None:
            stmt = stmt.where(NocEvent.status == status)
        if category is not None:
            stmt = stmt.where(NocEvent.category == category)
        if source is not None:
            stmt = stmt.where(NocEvent.source == source)
        if assigned_analyst_id is not None:
            stmt = stmt.where(NocEvent.assigned_analyst_id == assigned_analyst_id)
        if since is not None:
            stmt = stmt.where(NocEvent.triggered_at >= since)
        if until is not None:
            stmt = stmt.where(NocEvent.triggered_at <= until)
        stmt = stmt.order_by(NocEvent.triggered_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        events = list(result.scalars().all())
        for event in events:
            event.site_name = event.site.name if event.site else None
            event.device_name = event.device.name if event.device else None
            if event.assigned_analyst is not None:
                event.assigned_analyst_name = event.assigned_analyst.name
        return events

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        category: str | None = None,
        source: str | None = None,
        assigned_analyst_id: uuid.UUID | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(NocEvent).where(
            NocEvent.tenant_id == tenant_id,
            NocEvent.is_deleted.is_(False),
        )
        if site_id is not None:
            stmt = stmt.where(NocEvent.site_id == site_id)
        if device_id is not None:
            stmt = stmt.where(NocEvent.device_id == device_id)
        if severity is not None:
            stmt = stmt.where(NocEvent.severity == severity)
        if status is not None:
            stmt = stmt.where(NocEvent.status == status)
        if category is not None:
            stmt = stmt.where(NocEvent.category == category)
        if source is not None:
            stmt = stmt.where(NocEvent.source == source)
        if assigned_analyst_id is not None:
            stmt = stmt.where(NocEvent.assigned_analyst_id == assigned_analyst_id)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    # -- Aggregates --

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        stmt = (
            select(NocEvent.severity, NocEvent.status, NocEvent.category, func.count())
            .where(NocEvent.tenant_id == tenant_id, NocEvent.is_deleted.is_(False))
            .group_by(NocEvent.severity, NocEvent.status, NocEvent.category)
        )
        result = await self.db.execute(stmt)

        by_severity = dict.fromkeys(_ALL_SEVERITIES, 0)
        by_status = dict.fromkeys(_ALL_STATUSES, 0)
        by_category = dict.fromkeys(_ALL_CATEGORIES, 0)
        total = 0
        for severity, status, category, count in result.all():
            by_severity[severity] = by_severity.get(severity, 0) + count
            by_status[status] = by_status.get(status, 0) + count
            cat_key = category if category in _ALL_CATEGORIES else "other"
            by_category[cat_key] = by_category.get(cat_key, 0) + count
            total += count

        unassigned_stmt = select(func.count()).select_from(NocEvent).where(
            NocEvent.tenant_id == tenant_id,
            NocEvent.is_deleted.is_(False),
            NocEvent.status == "open",
            NocEvent.assigned_analyst_id.is_(None),
        )
        unassigned_open = (await self.db.execute(unassigned_stmt)).scalar_one()

        return {
            "total": total,
            "by_severity": by_severity,
            "by_status": by_status,
            "by_category": by_category,
            "unassigned_open": unassigned_open,
        }

    async def statistics(self, tenant_id: uuid.UUID) -> dict:
        """Extended statistics for the NOC dashboard."""
        base = await self.summary(tenant_id)

        mttr_stmt = (
            select(
                NocEvent.severity,
                func.avg(
                    func.extract("epoch", NocEvent.resolved_at - NocEvent.triggered_at) / 60.0
                ),
            )
            .where(
                NocEvent.tenant_id == tenant_id,
                NocEvent.is_deleted.is_(False),
                NocEvent.status == "resolved",
                NocEvent.resolved_at.isnot(None),
            )
            .group_by(NocEvent.severity)
        )
        mttr_result = await self.db.execute(mttr_stmt)
        mttr_by_severity: dict[str, float | None] = {}
        overall_mttrs: list[float] = []
        for severity, avg_minutes in mttr_result.all():
            val = round(float(avg_minutes), 1) if avg_minutes is not None else None
            mttr_by_severity[severity] = val
            if val is not None:
                overall_mttrs.append(val)
        overall_mttr = round(sum(overall_mttrs) / len(overall_mttrs), 1) if overall_mttrs else None

        thirty_min_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
        over_30_stmt = select(func.count()).select_from(NocEvent).where(
            NocEvent.tenant_id == tenant_id,
            NocEvent.is_deleted.is_(False),
            NocEvent.status == "open",
            NocEvent.triggered_at < thirty_min_ago,
        )
        open_over_30 = (await self.db.execute(over_30_stmt)).scalar_one()

        top_sources_stmt = (
            select(NocEvent.source, func.count())
            .where(NocEvent.tenant_id == tenant_id, NocEvent.is_deleted.is_(False))
            .group_by(NocEvent.source)
            .order_by(func.count().desc())
            .limit(5)
        )
        top_sources_result = await self.db.execute(top_sources_stmt)
        top_sources = [
            {"source": source, "count": count}
            for source, count in top_sources_result.all()
        ]

        return {
            **base,
            "mttr_minutes": overall_mttr,
            "mttr_by_severity": mttr_by_severity,
            "open_over_30min": open_over_30,
            "top_sources": top_sources,
        }

    async def count_by_analyst(
        self, tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, dict[str, int]]:
        """Count NOC events per assigned analyst — used by SOC roster."""
        stmt = (
            select(
                NocEvent.assigned_analyst_id,
                NocEvent.status,
                func.count(),
            )
            .where(
                NocEvent.tenant_id == tenant_id,
                NocEvent.is_deleted.is_(False),
                NocEvent.assigned_analyst_id.isnot(None),
            )
            .group_by(NocEvent.assigned_analyst_id, NocEvent.status)
        )
        result = await self.db.execute(stmt)
        counts: dict[uuid.UUID, dict[str, int]] = {}
        for analyst_id, status, count in result.all():
            if analyst_id not in counts:
                counts[analyst_id] = {"open": 0, "acknowledged": 0, "resolved": 0, "total": 0}
            counts[analyst_id][status] = counts[analyst_id].get(status, 0) + count
            counts[analyst_id]["total"] += count
        return counts

    async def list_for_analyst(
        self,
        tenant_id: uuid.UUID,
        analyst_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[NocEvent]:
        return await self.list(
            tenant_id,
            assigned_analyst_id=analyst_id,
            status=status,
            limit=limit,
        )

    async def last_assigned_at(
        self, tenant_id: uuid.UUID, analyst_id: uuid.UUID
    ) -> datetime | None:
        stmt = (
            select(func.max(NocEvent.created_at))
            .where(
                NocEvent.tenant_id == tenant_id,
                NocEvent.assigned_analyst_id == analyst_id,
                NocEvent.is_deleted.is_(False),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
