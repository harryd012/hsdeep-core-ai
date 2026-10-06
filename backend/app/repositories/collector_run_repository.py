"""
Repository Pattern: all direct SQLAlchemy access for CollectorRun lives here.
The service layer (CollectionOrchestrator) talks to this class only, same
convention as MonitoringSourceRepository / AlertRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
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

    async def counts_by_status(
        self, tenant_id: uuid.UUID, *, since: datetime | None = None
    ) -> dict[str, int]:
        """Run count per persisted ``status``, in ONE query (PHASE 6A).

        Mirrors ``DeviceRepository.counts_by_health``: a single GROUP BY instead
        of one COUNT per status, so the dashboard's collection KPIs cost one
        aggregate rather than five. Keys are exactly the statuses the model
        defines (running | success | partial | error | skipped); a status that
        has no rows in the window is simply absent from the mapping, which the
        service reads as 0.
        """
        stmt = select(CollectorRun.status, func.count()).where(
            CollectorRun.tenant_id == tenant_id
        )
        if since is not None:
            stmt = stmt.where(CollectorRun.started_at >= since)
        stmt = stmt.group_by(CollectorRun.status)
        return {status: count for status, count in (await self.db.execute(stmt)).all()}

    async def success_source_ids(
        self, tenant_id: uuid.UUID, *, since: datetime | None = None
    ) -> set[uuid.UUID]:
        """Distinct source ids with at least one SUCCESS run in the window.

        The coverage numerator (PHASE 6A): "which configured sources did this
        platform actually manage to collect from". Returns ids only - never run
        rows - so coverage does not depend on how many runs happened, and cannot
        be inflated by a source that is collected far more often than its peers.

        ``status`` vocabulary note: only ``"success"`` counts here. ``"partial"``
        is treated as a success for the *rate* KPI (per the CollectorHealthService
        contract) but deliberately does NOT count as coverage, because a partial
        run did not deliver the source's full inventory.
        """
        stmt = (
            select(CollectorRun.monitoring_source_id)
            .where(CollectorRun.tenant_id == tenant_id, CollectorRun.status == "success")
            .distinct()
        )
        if since is not None:
            stmt = stmt.where(CollectorRun.started_at >= since)
        result = await self.db.execute(stmt)
        return {row[0] for row in result.all()}

    async def stats_by_source(
        self, tenant_id: uuid.UUID, *, since: datetime
    ) -> dict[uuid.UUID, dict[str, float | None]]:
        """Per-source run aggregates for the window, in ONE GROUP BY (P1).

        Returns ``{source_id: {runs, succeeded, partial, failed, skipped,
        running, avg_duration_ms}}`` so the Command Center's collection-health
        rows can report success rate AND latency per source without a single
        per-source query (the N+1 the P1 spec forbids).

        ``avg_duration_ms`` is averaged only over runs that actually finished;
        ``None`` means no run in the window finished, which the service turns
        into a null rate — never a fabricated number.
        """
        finished = CollectorRun.finished_at.is_not(None)
        stmt = (
            select(
                CollectorRun.monitoring_source_id,
                func.count().label("runs"),
                func.count().filter(CollectorRun.status == "success").label("succeeded"),
                func.count().filter(CollectorRun.status == "partial").label("partial"),
                func.count().filter(CollectorRun.status == "error").label("failed"),
                func.count().filter(CollectorRun.status == "skipped").label("skipped"),
                func.count().filter(CollectorRun.status == "running").label("running"),
                func.avg(
                    func.extract("epoch", CollectorRun.finished_at - CollectorRun.started_at)
                    * 1000.0
                ).filter(finished).label("avg_duration_ms"),
            )
            .where(CollectorRun.tenant_id == tenant_id, CollectorRun.started_at >= since)
            .group_by(CollectorRun.monitoring_source_id)
        )
        out: dict[uuid.UUID, dict[str, float | None]] = {}
        for row in (await self.db.execute(stmt)).all():
            out[row[0]] = {
                "runs": int(row[1]),
                "succeeded": int(row[2]),
                "partial": int(row[3]),
                "failed": int(row[4]),
                "skipped": int(row[5]),
                "running": int(row[6]),
                "avg_duration_ms": float(row[7]) if row[7] is not None else None,
            }
        return out

    async def latest_by_source(
        self, tenant_id: uuid.UUID
    ) -> dict[uuid.UUID, CollectorRun]:
        """Most recent run per source, in ONE grouped-join query (P1).

        Powers "last_collection" + the latest error shown on each collection-
        health row: a GROUP BY max per source joined back to its run, so the
        cost stays one statement regardless of how many sources exist.
        """
        newest = (
            select(
                CollectorRun.monitoring_source_id.label("msid"),
                func.max(CollectorRun.started_at).label("max_started"),
            )
            .where(CollectorRun.tenant_id == tenant_id)
            .group_by(CollectorRun.monitoring_source_id)
            .subquery()
        )
        stmt = (
            select(CollectorRun)
            .join(
                newest,
                and_(
                    CollectorRun.monitoring_source_id == newest.c.msid,
                    CollectorRun.started_at == newest.c.max_started,
                ),
            )
            .where(CollectorRun.tenant_id == tenant_id)
        )
        result = await self.db.execute(stmt)
        return {
            run.monitoring_source_id: run
            for run in result.scalars().all()
            if run.monitoring_source_id is not None
        }

    async def hourly_failure_counts(
        self, tenant_id: uuid.UUID, *, since: datetime
    ) -> dict[datetime, int]:
        """``{hour: failed run count}`` for the window, in ONE GROUP BY (P1).

        The collection-failure trend series. Buckets are truncations of
        ``started_at`` (when the run began), never inferred from completion.
        """
        hour = func.date_trunc("hour", CollectorRun.started_at)
        stmt = (
            select(hour, func.count())
            .where(
                CollectorRun.tenant_id == tenant_id,
                CollectorRun.started_at >= since,
                CollectorRun.status == "error",
            )
            .group_by(hour)
        )
        return {bucket: count for bucket, count in (await self.db.execute(stmt)).all()}

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
