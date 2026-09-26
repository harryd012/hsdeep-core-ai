"""
Repository Pattern: all direct SQLAlchemy access for MonitoringSource lives
here. The service layer talks to this class only - it never issues its own
queries. This keeps persistence concerns swappable/mockable independently of
business logic (e.g. for unit tests).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Interval, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.monitoring_source import MonitoringSource


class MonitoringSourceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> MonitoringSource:
        source = MonitoringSource(**fields)
        self.db.add(source)
        await self.db.flush()
        await self.db.refresh(source)
        return source

    async def get(self, source_id: uuid.UUID, tenant_id: uuid.UUID) -> MonitoringSource | None:
        stmt = select(MonitoringSource).where(
            MonitoringSource.id == source_id,
            MonitoringSource.tenant_id == tenant_id,
            MonitoringSource.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        site_id: uuid.UUID | None = None,
        source_type: str | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[MonitoringSource]:
        stmt = select(MonitoringSource).where(
            MonitoringSource.tenant_id == tenant_id,
            MonitoringSource.is_deleted.is_(False),
        )
        if site_id is not None:
            stmt = stmt.where(MonitoringSource.site_id == site_id)
        if source_type is not None:
            stmt = stmt.where(MonitoringSource.source_type == source_type)
        if is_active is not None:
            stmt = stmt.where(MonitoringSource.is_active == is_active)
        if search:
            like = f"%{search}%"
            stmt = stmt.where((MonitoringSource.name.ilike(like)) | (MonitoringSource.hostname.ilike(like)))
        stmt = stmt.order_by(MonitoringSource.name)
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: uuid.UUID,
        site_id: uuid.UUID | None = None,
        source_type: str | None = None,
        is_active: bool | None = None,
        search: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(MonitoringSource).where(
            MonitoringSource.tenant_id == tenant_id,
            MonitoringSource.is_deleted.is_(False),
        )
        if site_id is not None:
            stmt = stmt.where(MonitoringSource.site_id == site_id)
        if source_type is not None:
            stmt = stmt.where(MonitoringSource.source_type == source_type)
        if is_active is not None:
            stmt = stmt.where(MonitoringSource.is_active == is_active)
        if search:
            like = f"%{search}%"
            stmt = stmt.where((MonitoringSource.name.ilike(like)) | (MonitoringSource.hostname.ilike(like)))
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def update(self, source: MonitoringSource, **fields) -> MonitoringSource:
        for key, value in fields.items():
            setattr(source, key, value)
        await self.db.flush()
        await self.db.refresh(source)
        return source

    async def mark_sync_result(
        self,
        source: MonitoringSource,
        status: str,
        error: str | None = None,
    ) -> MonitoringSource:
        source.status = status
        source.last_error = error
        source.last_sync_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(source)
        return source

    async def soft_delete(self, source: MonitoringSource) -> None:
        source.is_deleted = True
        source.deleted_at = datetime.now(timezone.utc)
        source.is_active = False
        await self.db.flush()

    async def list_due(self, now: datetime) -> list[MonitoringSource]:
        """Sources due for collection: enabled, not soft-deleted, and either
        never synced or their poll_interval_seconds has elapsed since
        last_sync_at. Used by the scheduler's tick() job - not tenant-scoped
        (the scheduler processes all tenants' due sources in one tick)."""
        # poll_interval_seconds is a per-row column, so the "N seconds" side
        # of the interval has to be computed in SQL per row rather than as a
        # single fixed Python timedelta. Casting the column to text and then
        # to Postgres's INTERVAL type (`'<n> seconds'::interval`) lets the
        # comparison run entirely in SQL - no full-table scan into Python.
        seconds_interval = cast(
            func.concat(MonitoringSource.poll_interval_seconds, " seconds"), Interval
        )
        now_tz = cast(now, DateTime(timezone=True))
        stmt = select(MonitoringSource).where(
            MonitoringSource.is_active.is_(True),
            MonitoringSource.is_deleted.is_(False),
            # Skip sources whose stored credentials can no longer be
            # decrypted. Once a source is marked `credentials_invalid` it
            # stays out of the scheduler's due list until an operator
            # re-saves its credentials (which resets status back to
            # `pending`/`connected`).
            MonitoringSource.status != "credentials_invalid",
            or_(
                MonitoringSource.last_sync_at.is_(None),
                MonitoringSource.last_sync_at <= now_tz - seconds_interval,
            ),
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
