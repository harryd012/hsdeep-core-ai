"""
Automation Status service layer.

Computes the Dashboard's "Automation Status" card entirely from real,
already-stored data - MonitoringSource rows (for connection state) and
CollectorRun rows (for run history) - never from hardcoded or randomly
generated values (Requirement 1.8).

`next_run_at` on each `SourceRunHealth` is a best-effort estimate
(`last_sync_at + poll_interval_seconds`) rather than an authoritative
schedule, because there is no real scheduler wired up yet (the scheduler
tasks are a later phase of this spec). It is still an honest computation
derived from real stored fields, not a fake/placeholder value.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.schemas.dashboard import AutomationStatus, SourceRunHealth


class AutomationStatusService:
    def __init__(self, source_repo: MonitoringSourceRepository, run_repo: CollectorRunRepository):
        self.source_repo = source_repo
        self.run_repo = run_repo

    async def status(self, tenant_id: uuid.UUID) -> AutomationStatus:
        sources = await self.source_repo.list(tenant_id)
        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        # limit is set high because this window is meant to capture every
        # run in the last 24h for the tenant, not a paginated slice.
        runs_24h = await self.run_repo.list(tenant_id, since=since_24h, limit=100000)

        sources_connected = sum(1 for s in sources if s.status == "connected")
        sources_error = sum(1 for s in sources if s.status == "error")
        runs_succeeded = sum(1 for r in runs_24h if r.status == "success")
        success_rate_pct = (runs_succeeded / len(runs_24h) * 100.0) if runs_24h else 0.0

        source_healths = [await self._source_health(tenant_id, source, runs_24h) for source in sources]

        return AutomationStatus(
            sources_total=len(sources),
            sources_connected=sources_connected,
            sources_error=sources_error,
            runs_last_24h=len(runs_24h),
            runs_succeeded_last_24h=runs_succeeded,
            success_rate_pct=round(success_rate_pct, 1),
            sources=source_healths,
        )

    async def _source_health(
        self, tenant_id: uuid.UUID, source, runs_24h: list
    ) -> SourceRunHealth:
        """Per-source last/next run health.

        `latest` prefers a run from the already-fetched 24h window (no extra
        query needed for the common case). If the source has no runs in the
        last 24h, a small fallback query fetches its single most recent run
        (regardless of age) so `last_run_at`/`last_run_status` reflect the
        true most recent run rather than silently looking empty.
        """
        source_runs_24h = [r for r in runs_24h if r.monitoring_source_id == source.id]
        if source_runs_24h:
            latest = max(source_runs_24h, key=lambda r: r.started_at)
        else:
            fallback = await self.run_repo.list(tenant_id, monitoring_source_id=source.id, limit=1)
            latest = fallback[0] if fallback else None

        # Best-effort estimate pending a real scheduler (see module docstring).
        next_run_at = None
        if source.last_sync_at is not None:
            next_run_at = source.last_sync_at + timedelta(seconds=source.poll_interval_seconds)

        return SourceRunHealth(
            source_id=source.id,
            source_name=source.name,
            status=source.status,
            last_run_at=latest.started_at if latest else None,
            last_run_status=latest.status if latest else None,
            next_run_at=next_run_at,
        )
