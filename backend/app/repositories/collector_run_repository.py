"""
Repository Pattern: all direct SQLAlchemy access for CollectorRun lives here.
The service layer (CollectionOrchestrator) talks to this class only, same
convention as MonitoringSourceRepository / AlertRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collector_run import CollectorRun


class CollectorRunRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        monitoring_source_id: uuid.UUID,
        trigger: str = "scheduler",
    ) -> CollectorRun:
        run = CollectorRun(
            tenant_id=tenant_id,
            monitoring_source_id=monitoring_source_id,
            trigger=trigger,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        await self.db.flush()
        await self.db.refresh(run)
        return run

    async def finish(
        self,
        run: CollectorRun,
        *,
        status: str,
        error: str | None = None,
        devices_upserted: int = 0,
        interfaces_upserted: int = 0,
        sensors_upserted: int = 0,
        metrics_recorded: int = 0,
        events_recorded: int = 0,
        alerts_upserted: int = 0,
    ) -> CollectorRun:
        run.status = status
        run.error = error
        run.devices_upserted = devices_upserted
        run.interfaces_upserted = interfaces_upserted
        run.sensors_upserted = sensors_upserted
        run.metrics_recorded = metrics_recorded
        run.events_recorded = events_recorded
        run.alerts_upserted = alerts_upserted
        run.finished_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(run)
        return run

    async def get_running_for_source(self, monitoring_source_id: uuid.UUID) -> CollectorRun | None:
        """Used by the orchestrator's concurrent-run-skip guard. There should
        be at most one running row per source in practice, but we limit
        defensively."""
        stmt = (
            select(CollectorRun)
            .where(
                CollectorRun.monitoring_source_id == monitoring_source_id,
                CollectorRun.status == "running",
            )
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        monitoring_source_id: uuid.UUID | None = None,
        status: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CollectorRun]:
        stmt = select(CollectorRun).where(CollectorRun.tenant_id == tenant_id)
        if monitoring_source_id is not None:
            stmt = stmt.where(CollectorRun.monitoring_source_id == monitoring_source_id)
        if status is not None:
            stmt = stmt.where(CollectorRun.status == status)
        if since is not None:
            stmt = stmt.where(CollectorRun.started_at >= since)
        stmt = stmt.order_by(CollectorRun.started_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        monitoring_source_id: uuid.UUID | None = None,
        status: str | None = None,
        since: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(CollectorRun).where(CollectorRun.tenant_id == tenant_id)
        if monitoring_source_id is not None:
            stmt = stmt.where(CollectorRun.monitoring_source_id == monitoring_source_id)
        if status is not None:
            stmt = stmt.where(CollectorRun.status == status)
        if since is not None:
            stmt = stmt.where(CollectorRun.started_at >= since)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def list_stale_running(self, threshold: datetime) -> list[CollectorRun]:
        """Not tenant-scoped - this powers a global startup reconciliation
        sweep across all tenants for runs that got stuck in 'running' (e.g.
        the process died mid-run)."""
        stmt = select(CollectorRun).where(
            CollectorRun.status == "running",
            CollectorRun.started_at < threshold,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
