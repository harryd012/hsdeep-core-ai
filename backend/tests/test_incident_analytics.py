"""
Incident Analytics aggregation (GET /api/dashboard/incident-analytics).

Guards the truth rules this payload is most exposed to:

* the trend axis is continuous: days with no incidents are explicit zeros and
  the series has exactly ``days`` points;
* percentiles with no resolved incidents are None - never 0 minutes (which
  would read as "instant recovery");
* the heatmap is always 7 x 24, Monday-first, and lands counts in the right
  cell (ISO weekday 1 = Monday);
* aging/duration buckets always come back in fixed display order with every
  bucket present, so the chart axis never reorders or drops a bar;
* ``other_sites_count`` is exactly opened-in-window minus the listed sites, so
  the "Other" bar can never double-count or go negative;
* the response validates against the Pydantic schema (what FastAPI serves).

Pure-python over a fake repository, so no database is needed. The SQL itself
(percentile_cont, timezone bucketing, partial-index predicates) is exercised
by the DB-backed suites / manual verification against PostgreSQL.
"""
from __future__ import annotations

import datetime as dt
import uuid

from app.schemas.incident_analytics import IncidentAnalytics
from app.services.incident_analytics_service import (
    HEATMAP_DAYS,
    UNASSIGNED_SITE_LABEL,
    IncidentAnalyticsService,
)

TENANT = uuid.uuid4()
# Wednesday 2025-02-05 14:32 UTC
NOW = dt.datetime(2025, 2, 5, 14, 32, tzinfo=dt.timezone.utc)


class _FakeRepo:
    def __init__(
        self,
        opened=None,
        resolved=None,
        severity=None,
        aging=None,
        heatmap=None,
        buckets=None,
        percentiles=(0, None, None, None),
        sites=None,
    ):
        self.opened = opened or {}
        self.resolved = resolved or {}
        self.severity = severity or {}
        self.aging = aging or []
        self.heatmap = heatmap or []
        self.buckets = buckets or {}
        self.percentiles = percentiles
        self.sites = sites or []
        self.calls: list[tuple] = []

    async def opened_by_day(self, tenant_id, since, scope=None):
        self.calls.append(("opened_by_day", tenant_id, since))
        return self.opened

    async def resolved_by_day(self, tenant_id, since, scope=None):
        self.calls.append(("resolved_by_day", tenant_id, since))
        return self.resolved

    async def severity_counts(self, tenant_id, since, scope=None):
        return self.severity

    async def open_aging(self, tenant_id, scope=None):
        self.calls.append(("open_aging", tenant_id))
        return self.aging

    async def opened_heatmap(self, tenant_id, since, scope=None):
        self.calls.append(("opened_heatmap", tenant_id, since))
        return self.heatmap

    async def resolved_duration_buckets(self, tenant_id, since, scope=None):
        return self.buckets

    async def resolved_duration_percentiles(self, tenant_id, since, scope=None):
        return self.percentiles

    async def top_sites(self, tenant_id, since, limit, scope=None):
        self.calls.append(("top_sites", tenant_id, since, limit))
        return self.sites


async def _snapshot(repo: _FakeRepo, days: int = 7) -> IncidentAnalytics:
    return await IncidentAnalyticsService(repo).snapshot(TENANT, days=days, now=NOW)


async def test_empty_tenant_returns_explicit_zeros_and_none_percentiles():
    result = await _snapshot(_FakeRepo())

    assert result.total_opened == 0
    assert result.severity == []
    assert result.top_sites == []
    assert result.other_sites_count == 0
    # No resolved incidents: percentiles are None, NOT 0.0 minutes.
    assert result.duration.resolved_count == 0
    assert result.duration.median_minutes is None
    assert result.duration.p90_minutes is None
    assert result.duration.p95_minutes is None
    # ...but the axes are still complete so the chart can render an empty state.
    assert len(result.trend) == 7
    assert all(p.opened == 0 and p.resolved == 0 for p in result.trend)
    assert len(result.heatmap) == 7 and all(len(row) == 24 for row in result.heatmap)
    assert sum(sum(row) for row in result.heatmap) == 0


async def test_trend_is_continuous_ends_today_and_fills_gaps():
    today = NOW.date()
    two_days_ago = today - dt.timedelta(days=2)
    repo = _FakeRepo(opened={today: 5, two_days_ago: 3}, resolved={today: 2})

    result = await _snapshot(repo, days=7)

    days = [p.day for p in result.trend]
    assert days == [today - dt.timedelta(days=6 - i) for i in range(7)]
    by_day = {p.day: p for p in result.trend}
    assert (by_day[today].opened, by_day[today].resolved) == (5, 2)
    assert (by_day[two_days_ago].opened, by_day[two_days_ago].resolved) == (3, 0)
    assert by_day[today - dt.timedelta(days=1)].opened == 0  # measured zero


async def test_window_starts_on_a_whole_utc_day_and_heatmap_is_fixed_28_days():
    repo = _FakeRepo()
    await _snapshot(repo, days=7)

    opened_since = next(c for c in repo.calls if c[0] == "opened_by_day")[2]
    heat_since = next(c for c in repo.calls if c[0] == "opened_heatmap")[2]
    assert opened_since == dt.datetime(2025, 1, 30, 0, 0, tzinfo=dt.timezone.utc)
    assert heat_since == dt.datetime(2025, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    assert (NOW.date() - heat_since.date()).days == HEATMAP_DAYS - 1


async def test_every_repository_call_is_scoped_to_the_tenant():
    repo = _FakeRepo()
    await _snapshot(repo)
    assert repo.calls, "service made no repository calls"
    assert all(call[1] == TENANT for call in repo.calls)


async def test_severity_sorted_by_count_and_total_matches():
    repo = _FakeRepo(severity={"warning": 4, "critical": 9, "info": 4})
    result = await _snapshot(repo)

    assert [(s.severity, s.count) for s in result.severity] == [
        ("critical", 9),
        ("info", 4),  # ties break alphabetically, deterministically
        ("warning", 4),
    ]
    assert result.total_opened == 17


async def test_heatmap_is_monday_first_and_accumulates():
    # ISO weekday 1 = Monday, hour 9; ISO weekday 7 = Sunday, hour 23.
    repo = _FakeRepo(heatmap=[(1, 9, 4), (1, 9, 2), (7, 23, 1), (9, 99, 50)])
    result = await _snapshot(repo)

    assert result.heatmap[0][9] == 6  # Monday 09:00, accumulated
    assert result.heatmap[6][23] == 1  # Sunday 23:00
    assert sum(sum(row) for row in result.heatmap) == 7  # out-of-range row dropped


async def test_aging_buckets_are_complete_and_ordered():
    repo = _FakeRepo(
        aging=[("1_4h", "critical", 3), ("1_4h", "warning", 2), ("gt_3d", "info", 1)]
    )
    result = await _snapshot(repo)

    assert [b.key for b in result.aging] == ["lt_1h", "1_4h", "4_24h", "1_3d", "gt_3d"]
    by_key = {b.key: b for b in result.aging}
    assert by_key["1_4h"].total == 5
    assert by_key["1_4h"].by_severity == {"critical": 3, "warning": 2}
    assert by_key["lt_1h"].total == 0 and by_key["lt_1h"].by_severity == {}
    assert by_key["gt_3d"].total == 1


async def test_duration_percentiles_convert_seconds_to_minutes_and_fill_buckets():
    repo = _FakeRepo(
        buckets={"30m_2h": 12, "gt_24h": 1},
        percentiles=(13, 3360.0, 15120.0, 35280.0),  # 56m / 4.2h / 9.8h
    )
    result = await _snapshot(repo)

    d = result.duration
    assert d.resolved_count == 13
    assert d.median_minutes == 56.0
    assert d.p90_minutes == 252.0
    assert d.p95_minutes == 588.0
    assert [b.key for b in d.buckets] == [
        "lt_5m", "5_30m", "30m_2h", "2h_8h", "8h_24h", "gt_24h",
    ]
    assert {b.key: b.count for b in d.buckets}["30m_2h"] == 12
    assert {b.key: b.count for b in d.buckets}["lt_5m"] == 0


async def test_other_sites_is_remainder_and_unassigned_is_labelled():
    sid = uuid.uuid4()
    repo = _FakeRepo(
        severity={"critical": 20},
        sites=[(sid, "New York", 9), (None, None, 4)],
    )
    result = await _snapshot(repo)

    assert [(s.name, s.count) for s in result.top_sites] == [
        ("New York", 9),
        (UNASSIGNED_SITE_LABEL, 4),
    ]
    assert result.top_sites[1].site_id is None
    assert result.other_sites_count == 7  # 20 opened - 13 listed


async def test_other_sites_never_negative():
    repo = _FakeRepo(severity={"critical": 2}, sites=[(uuid.uuid4(), "A", 5)])
    result = await _snapshot(repo)
    assert result.other_sites_count == 0


async def test_payload_validates_against_the_served_schema():
    repo = _FakeRepo(
        opened={NOW.date(): 1},
        severity={"critical": 1},
        sites=[(uuid.uuid4(), "HQ", 1)],
        percentiles=(1, 60.0, 60.0, 60.0),
    )
    result = await _snapshot(repo)

    round_tripped = IncidentAnalytics.model_validate(result.model_dump(mode="json"))
    assert round_tripped.window_days == 7
    assert round_tripped.heatmap_days == HEATMAP_DAYS


async def test_filters_reach_every_repository_aggregate():
    """ECC-1: site/severity narrow ALL charts, not just some of them."""
    from app.repositories.incident_analytics_repository import IncidentScope

    seen: list = []

    class _ScopeRepo(_FakeRepo):
        def __getattribute__(self, name):
            attr = super().__getattribute__(name)
            if name in {
                "opened_by_day", "resolved_by_day", "severity_counts", "open_aging",
                "opened_heatmap", "resolved_duration_buckets",
                "resolved_duration_percentiles", "top_sites",
            }:
                async def wrapped(*args):
                    # The service passes scope positionally, always last.
                    seen.append((name, args[-1]))
                    return await attr(*args)
                return wrapped
            return attr

    site = uuid.uuid4()
    await IncidentAnalyticsService(_ScopeRepo()).snapshot(
        TENANT, days=7, now=NOW, site_id=site, severity="critical"
    )
    assert len(seen) == 8
    assert all(scope == IncidentScope(site_id=site, severity="critical") for _, scope in seen)


async def test_no_filters_means_empty_scope():
    from app.repositories.incident_analytics_repository import IncidentScope

    seen: list = []

    class _ScopeRepo(_FakeRepo):
        async def severity_counts(self, tenant_id, since, scope=None):
            seen.append(scope)
            return {}

    await IncidentAnalyticsService(_ScopeRepo()).snapshot(TENANT, days=7, now=NOW)
    assert seen == [IncidentScope()]
