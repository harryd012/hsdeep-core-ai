"""
Incident Analytics service (Executive Command Center charts).

Answers: "how is incident load trending, how severe is it, how long are open
incidents aging, when do incidents start, how fast do we resolve them, and
which sites are affected?" - as ONE tenant-scoped, read-only payload
(GET /api/dashboard/incident-analytics).

Design constraints (same as OperationalIntelligenceService):
  * Every number comes from a persisted Incident column through a SQL
    aggregate in IncidentAnalyticsRepository. No counters are stored, nothing
    is estimated, and no incident rows are loaded into Python.
  * A FIXED query budget independent of tenant size:
        2x trend (opened/resolved by day)   1x severity   1x aging
        1x heatmap   3x duration (buckets + percentiles)   1x top sites
    = 8 statements, run sequentially (one AsyncSession cannot run concurrently).
  * Truthful absence: percentiles with no resolved incidents are None, never
    0 minutes. Days with no incidents are explicit zeros so the axis is
    continuous - that is a measured zero, not a fabricated reading.
  * Tenant scope is applied by the repository, in SQL, on every statement.
"""
from __future__ import annotations

import datetime as dt
import uuid

from app.repositories.incident_analytics_repository import (
    AGING_EDGES,
    AGING_OVERFLOW_KEY,
    DURATION_EDGES,
    DURATION_OVERFLOW_KEY,
    IncidentAnalyticsRepository,
    IncidentScope,
)
from app.schemas.incident_analytics import (
    AgingBucket,
    DurationBucket,
    DurationStats,
    IncidentAnalytics,
    IncidentTrendPoint,
    SeverityCount,
    SiteIncidentCount,
)

HEATMAP_DAYS = 28  # "Last 4 weeks" - fixed so the 7x24 grid stays comparable
TOP_SITES_LIMIT = 7  # the 8th bar is "Other", computed from the remainder
UNASSIGNED_SITE_LABEL = "Unassigned"

AGING_LABELS: dict[str, str] = {
    "lt_1h": "< 1h",
    "1_4h": "1-4h",
    "4_24h": "4-24h",
    "1_3d": "1-3d",
    "gt_3d": "> 3d",
}
DURATION_LABELS: dict[str, str] = {
    "lt_5m": "< 5m",
    "5_30m": "5m-30m",
    "30m_2h": "30m-2h",
    "2h_8h": "2h-8h",
    "8h_24h": "8h-24h",
    "gt_24h": "> 24h",
}


def _start_of_day(value: dt.datetime) -> dt.datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _minutes(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds / 60.0, 1)


class IncidentAnalyticsService:
    def __init__(self, repo: IncidentAnalyticsRepository):
        self.repo = repo

    async def snapshot(
        self,
        tenant_id: uuid.UUID,
        days: int = 7,
        now: dt.datetime | None = None,
        site_id: uuid.UUID | None = None,
        severity: str | None = None,
    ) -> IncidentAnalytics:
        """``site_id`` / ``severity`` narrow EVERY aggregate (ECC-1 filters);
        both ``None`` reproduces the unfiltered payload exactly."""
        now = now or dt.datetime.now(dt.timezone.utc)
        scope = IncidentScope(site_id=site_id, severity=severity)
        # Whole UTC calendar days, ending today, so every bucket is complete
        # on the left edge and the trend axis has exactly `days` points.
        window_start = _start_of_day(now - dt.timedelta(days=days - 1))
        heatmap_start = _start_of_day(now - dt.timedelta(days=HEATMAP_DAYS - 1))

        opened = await self.repo.opened_by_day(tenant_id, window_start, scope)
        resolved = await self.repo.resolved_by_day(tenant_id, window_start, scope)
        trend = [
            IncidentTrendPoint(day=d, opened=opened.get(d, 0), resolved=resolved.get(d, 0))
            for d in (window_start.date() + dt.timedelta(days=i) for i in range(days))
        ]

        severity_map = await self.repo.severity_counts(tenant_id, window_start, scope)
        severity = [
            SeverityCount(severity=name, count=count)
            for name, count in sorted(severity_map.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        total_opened = sum(severity_map.values())

        aging = self._build_aging(await self.repo.open_aging(tenant_id, scope))
        heatmap = self._build_heatmap(await self.repo.opened_heatmap(tenant_id, heatmap_start, scope))

        buckets = await self.repo.resolved_duration_buckets(tenant_id, window_start, scope)
        resolved_count, p50, p90, p95 = await self.repo.resolved_duration_percentiles(
            tenant_id, window_start, scope
        )
        duration = DurationStats(
            resolved_count=resolved_count,
            median_minutes=_minutes(p50),
            p90_minutes=_minutes(p90),
            p95_minutes=_minutes(p95),
            buckets=[
                DurationBucket(key=key, label=DURATION_LABELS[key], count=buckets.get(key, 0))
                for key in [k for k, _ in DURATION_EDGES] + [DURATION_OVERFLOW_KEY]
            ],
        )

        top_rows = await self.repo.top_sites(tenant_id, window_start, TOP_SITES_LIMIT, scope)
        top_sites = [
            SiteIncidentCount(
                site_id=site_id,
                name=name or UNASSIGNED_SITE_LABEL,
                count=count,
            )
            for site_id, name, count in top_rows
        ]
        other = max(0, total_opened - sum(s.count for s in top_sites))

        return IncidentAnalytics(
            generated_at=now,
            window_days=days,
            heatmap_days=HEATMAP_DAYS,
            trend=trend,
            severity=severity,
            total_opened=total_opened,
            aging=aging,
            heatmap=heatmap,
            duration=duration,
            top_sites=top_sites,
            other_sites_count=other,
        )

    @staticmethod
    def _build_aging(rows: list[tuple[str, str, int]]) -> list[AgingBucket]:
        by_bucket: dict[str, dict[str, int]] = {}
        for bucket_key, severity, count in rows:
            by_bucket.setdefault(bucket_key, {})
            by_bucket[bucket_key][severity] = by_bucket[bucket_key].get(severity, 0) + count
        ordered = [k for k, _ in AGING_EDGES] + [AGING_OVERFLOW_KEY]
        return [
            AgingBucket(
                key=key,
                label=AGING_LABELS[key],
                total=sum(by_bucket.get(key, {}).values()),
                by_severity=by_bucket.get(key, {}),
            )
            for key in ordered
        ]

    @staticmethod
    def _build_heatmap(rows: list[tuple[int, int, int]]) -> list[list[int]]:
        grid = [[0] * 24 for _ in range(7)]
        for iso_weekday, hour, count in rows:
            if 1 <= iso_weekday <= 7 and 0 <= hour <= 23:
                grid[iso_weekday - 1][hour] += count
        return grid
