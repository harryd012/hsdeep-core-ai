"""
Repository Pattern: all direct SQLAlchemy access for SourceHealthAudit lives
here. The ServiceHealthManager talks to this class only.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_health_audit import SourceHealthAudit


class SourceHealthAuditRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        event_type: str,
        reason: str,
        collector_run_id: uuid.UUID | None = None,
        from_health_status: str | None = None,
        to_health_status: str | None = None,
        from_consecutive_failures: int | None = None,
        to_consecutive_failures: int | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
    ) -> SourceHealthAudit:
        audit = SourceHealthAudit(
            tenant_id=tenant_id,
            monitoring_source_id=monitoring_source_id,
            collector_run_id=collector_run_id,
            event_type=event_type,
            from_health_status=from_health_status,
            to_health_status=to_health_status,
            from_consecutive_failures=from_consecutive_failures,
            to_consecutive_failures=to_consecutive_failures,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
        )
        self.db.add(audit)
        await self.db.flush()
        await self.db.refresh(audit)
        return audit

    async def list_for_source(
        self,
        monitoring_source_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[SourceHealthAudit]:
        stmt = (
            select(SourceHealthAudit)
            .where(SourceHealthAudit.monitoring_source_id == monitoring_source_id)
            .order_by(SourceHealthAudit.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[SourceHealthAudit]:
        stmt = (
            select(SourceHealthAudit)
            .where(SourceHealthAudit.tenant_id == tenant_id)
        )
        if since is not None:
            stmt = stmt.where(SourceHealthAudit.created_at >= since)
        stmt = stmt.order_by(SourceHealthAudit.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
