"""
Collector Health Service (Sprint 15).

Computes per-MonitoringSource health metrics from the CollectorRun history
table - success rate, failure rate, last success, last failure, retry count,
current state, and error details. This is the sprint's "Collector Health"
requirement, powering the collector-health dashboard widget.

Design: all metrics are derived from existing CollectorRun rows (immutable
audit records), so this service is read-only and stateless - it never
writes to the database, it only aggregates. Same "honest heuristic"
philosophy as the rest of the alerting pipeline: every number here is a
real count of real runs, never a simulated score.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collector_run import CollectorRun
from app.models.monitoring_source import MonitoringSource

#: Source types that are synthetic/test fixtures, not real collectors. Their
#: runs must not inflate a tenant's real failure counts on the dashboard. This
#: is a documented exclusion, not a data deletion (rows are left untouched).
TEST_SOURCE_TYPES: frozenset[str] = frozenset({"test_fake_concurrency"})


def categorize_failure(error: str | None) -> str | None:
    """Classify a collector's last error into a coarse reason bucket.

    The dashboard groups failures by WHY (credentials missing / unreachable /
    no collector registered / other) rather than one opaque count. Purely a
    presentation aid over the persisted ``last_error`` string - no secret or
    raw payload is surfaced.
    """
    if not error:
        return None
    text = error.lower()
    if any(
        token in text
        for token in (
            "credential", "authentication", "unauthorized", "password",
            "api key", "apikey", "token", "401", "403",
        )
    ):
        return "credentials"
    if any(
        token in text
        for token in (
            "unreachable", "timeout", "timed out", "connection refused",
            "no route", "network", "dns", "host is down", "connection reset",
        )
    ):
        return "unreachable"
    if any(
        token in text
        for token in ("no collector", "not registered", "collector not", "not found")
    ):
        return "no_collector_registered"
    return "other"


@dataclass
class CollectorHealth:
    """Health metrics for a single MonitoringSource's collector."""
    source_id: uuid.UUID
    source_name: str
    current_state: str  # healthy | degraded | failing | unknown
    success_rate_pct: float
    failure_rate_pct: float
    last_success_at: datetime | None
    last_failure_at: datetime | None
    total_runs: int
    successful_runs: int
    failed_runs: int
    retry_count: int
    last_error: str | None
    #: Coarse reason bucket for the last failure (credentials / unreachable /
    #: no_collector_registered / other / None). Presentation aid only.
    failure_category: str | None = None


class CollectorHealthService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_health_for_source(
        self, monitoring_source_id: uuid.UUID, *, window_hours: int = 24
    ) -> CollectorHealth:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        source_result = await self.db.execute(
            select(MonitoringSource.name).where(MonitoringSource.id == monitoring_source_id)
        )
        source_name = source_result.scalar_one_or_none() or "Unknown"
        return await self._compute_health(monitoring_source_id, source_name, cutoff)

    async def get_health_for_tenant(
        self,
        tenant_id: uuid.UUID,
        *,
        window_hours: int = 24,
        exclude_test_sources: bool = False,
    ) -> list[CollectorHealth]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        stmt = select(MonitoringSource.id, MonitoringSource.name).where(
            MonitoringSource.tenant_id == tenant_id,
            MonitoringSource.is_deleted.is_(False),
        )
        # Synthetic/test fixtures (e.g. a load generator left in the dev tenant)
        # must not inflate the real failure counts. Excluded by source_type, not
        # by deleting rows.
        if exclude_test_sources:
            stmt = stmt.where(MonitoringSource.source_type.notin_(TEST_SOURCE_TYPES))
        sources_result = await self.db.execute(stmt)
        sources = sources_result.all()
        health_list: list[CollectorHealth] = []
        for source_id, source_name in sources:
            health = await self._compute_health(source_id, source_name, cutoff)
            health_list.append(health)
        return health_list

    async def _compute_health(
        self, source_id: uuid.UUID, source_name: str, cutoff: datetime,
    ) -> CollectorHealth:
        total_stmt = select(func.count()).select_from(CollectorRun).where(
            CollectorRun.monitoring_source_id == source_id, CollectorRun.started_at >= cutoff,
        )
        total_runs = (await self.db.execute(total_stmt)).scalar_one()

        success_stmt = select(func.count()).select_from(CollectorRun).where(
            CollectorRun.monitoring_source_id == source_id,
            CollectorRun.started_at >= cutoff,
            CollectorRun.status.in_(("success", "partial")),
        )
        successful_runs = (await self.db.execute(success_stmt)).scalar_one()

        failed_stmt = select(func.count()).select_from(CollectorRun).where(
            CollectorRun.monitoring_source_id == source_id,
            CollectorRun.started_at >= cutoff,
            CollectorRun.status == "error",
        )
        failed_runs = (await self.db.execute(failed_stmt)).scalar_one()

        last_success_stmt = select(func.max(CollectorRun.finished_at)).where(
            CollectorRun.monitoring_source_id == source_id,
            CollectorRun.status.in_(("success", "partial")),
        )
        last_success_at = (await self.db.execute(last_success_stmt)).scalar_one_or_none()

        last_failure_stmt = (
            select(CollectorRun.finished_at, CollectorRun.error)
            .where(CollectorRun.monitoring_source_id == source_id, CollectorRun.status == "error")
            .order_by(CollectorRun.finished_at.desc()).limit(1)
        )
        last_failure_row = (await self.db.execute(last_failure_stmt)).first()
        last_failure_at = last_failure_row[0] if last_failure_row else None
        last_error = last_failure_row[1] if last_failure_row else None

        retry_count = 0
        if failed_runs > 0:
            retry_count = failed_runs

        if total_runs > 0:
            success_rate = (successful_runs / total_runs) * 100.0
            failure_rate = (failed_runs / total_runs) * 100.0
        else:
            success_rate = 0.0
            failure_rate = 0.0

        if total_runs == 0:
            current_state = "unknown"
        elif failure_rate >= 50.0:
            current_state = "failing"
        elif failure_rate > 0:
            current_state = "degraded"
        else:
            current_state = "healthy"

        return CollectorHealth(
            source_id=source_id, source_name=source_name,
            current_state=current_state, success_rate_pct=round(success_rate, 1),
            failure_rate_pct=round(failure_rate, 1),
            last_success_at=last_success_at, last_failure_at=last_failure_at,
            total_runs=total_runs, successful_runs=successful_runs,
            failed_runs=failed_runs, retry_count=retry_count, last_error=last_error,
            failure_category=categorize_failure(last_error),
        )