"""
PHASE 6A / SPRINT P1 - Command Center brief aggregation.

Guards the regressions this surface is most exposed to:

* the method must EXIST and be reachable from its route. It was once appended
  *inside* `snapshot`, where it was unreachable dead code and the endpoint
  raised AttributeError; a bare "does it return a brief" test catches that.
* every bucket set must SUM to its total, so no device, sensor or source can
  vanish from the estate.
* DEGRADED collection stays its own bucket, never folded into UNKNOWN.
* percentages stay on the 0-100 scale the backend returns (the panel once
  multiplied by 100 again and rendered "9850%").
* the honesty rule: no denominator means None - never 0% and never 100%.

Pure-python over fake repositories, so no database is needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.schemas.dashboard import CommandCenterBrief
from app.services.operational_intelligence_service import (
    OperationalIntelligenceService,
)


class _Source:
    def __init__(self, source_id, is_active=True, name="source", vendor="prtg"):
        self.id = source_id
        self.is_active = is_active
        self.name = name
        self.vendor = vendor
        self.source_type = "snmp"


def _stats(runs=0, succeeded=0, partial=0, failed=0, avg_duration_ms=None):
    """One source's run aggregates, shaped exactly like `stats_by_source`."""
    return {
        "runs": runs,
        "succeeded": succeeded,
        "partial": partial,
        "failed": failed,
        "skipped": 0,
        "running": 0,
        "avg_duration_ms": avg_duration_ms,
    }


class _FakeSourceRepo:
    def __init__(self, sources):
        self.sources = sources

    async def list(self, tenant_id, **kw):
        return self.sources


class _FakeRunRepo:
    def __init__(self, statuses=None, stats=None, latest=None):
        self.statuses = statuses or {}
        self.stats = stats or {}
        self.latest = latest or {}

    async def counts_by_status(self, tenant_id, **kw):
        return self.statuses

    async def success_source_ids(self, tenant_id, **kw):
        return set()

    async def stats_by_source(self, tenant_id, **kw):
        return self.stats

    async def latest_by_source(self, tenant_id):
        return self.latest

    async def hourly_failure_counts(self, tenant_id, **kw):
        return {}


class _FakeSensorRepo:
    def __init__(self, statuses=None, freshness=(0, 0, 0)):
        self.statuses = statuses or {}
        self.freshness = freshness

    async def counts_by_status(self, tenant_id, **kw):
        return self.statuses

    async def counts_by_freshness(self, tenant_id, cutoff):
        return self.freshness

    async def hourly_stale_telemetry_counts(self, tenant_id, **kw):
        # Asserted explicitly: this repo needs BOTH cutoffs, and passing only
        # `since` is a TypeError that no schema check would catch.
        assert "since" in kw and "freshness_cutoff" in kw, kw
        return {}


class _FakeDeviceRepo:
    def __init__(self, statuses=None, by_source=None):
        self.statuses = statuses or {}
        self.by_source = by_source or {}

    async def counts_by_status(self, tenant_id, **kw):
        return self.statuses

    async def counts_by_monitoring_source(self, tenant_id):
        return self.by_source

    async def hourly_down_counts(self, tenant_id, **kw):
        return {}


class _FakeAlertRepo:
    def __init__(self, critical=None, high=None):
        self.critical = critical or []
        self.high = high or []

    async def count(self, tenant_id, **kw):
        return 0

    async def list_active_for_insights(self, tenant_id, severity=None, limit=24):
        return self.critical if severity == "critical" else self.high

    async def hourly_counts(self, tenant_id, **kw):
        return {}

    async def mean_ack_minutes(self, tenant_id, **kw):
        return None


class _FakeIncidentRepo:
    def __init__(self, incidents=None):
        self.incidents = incidents or []

    async def list(self, tenant_id, **kw):
        return self.incidents

    async def count(self, tenant_id, **kw):
        return 0

    async def hourly_counts(self, tenant_id, **kw):
        return {}

    async def mean_resolution_minutes(self, tenant_id, **kw):
        return None


class _FakeTaskRepo:
    async def count_by_status(self, tenant_id, **kw):
        return {}


def _svc(**overrides) -> OperationalIntelligenceService:
    repos = {
        "source_repo": _FakeSourceRepo([]),
        "run_repo": _FakeRunRepo(),
        "sensor_repo": _FakeSensorRepo(),
        "device_repo": _FakeDeviceRepo(),
        "alert_repo": _FakeAlertRepo(),
        "task_repo": _FakeTaskRepo(),
        "incident_repo": _FakeIncidentRepo(),
    }
    repos.update(overrides)
    return OperationalIntelligenceService(**repos)


class TestBriefIsReachable:
    async def test_method_exists_and_returns_a_brief(self):
        """The endpoint calls `service.command_center_brief`; it must exist.

        This is the regression guard for the brief having been appended inside
        `snapshot`, where it was unreachable and the route raised AttributeError.
        """
        assert hasattr(OperationalIntelligenceService, "command_center_brief")

        brief = await _svc().command_center_brief(uuid.uuid4())
        assert isinstance(brief, CommandCenterBrief)
        assert brief.window_hours == 24

    async def test_snapshot_still_returns_its_own_payload(self):
        """Extracting the brief must not have changed what `snapshot` returns."""
        from app.schemas.dashboard import OperationalIntelligence

        snap = await _svc().snapshot(uuid.uuid4())
        assert isinstance(snap, OperationalIntelligence)

    async def test_route_is_registered_and_tenant_is_not_client_selectable(self):
        from fastapi.routing import APIRoute

        from app.main import app

        route = next(
            r
            for r in app.routes
            if isinstance(r, APIRoute)
            and r.path == "/api/dashboard/command-center-brief"
        )
        query_names = {p.name for p in route.dependant.query_params} | {
            p.alias for p in route.dependant.query_params
        }
        assert query_names == set(), f"expected no query params, got {query_names}"


class TestBucketSums:
    async def test_device_buckets_sum_to_total(self):
        """No device may vanish: an undefined status lands in `unknown`."""
        brief = await _svc(
            device_repo=_FakeDeviceRepo(
                {"up": 3, "warning": 1, "critical": 1, "down": 2, "weird": 4}
            )
        ).command_center_brief(uuid.uuid4())

        d = brief.infrastructure_health.devices
        assert d.total == 11
        assert d.healthy + d.warning + d.critical + d.down + d.unknown == d.total
        assert d.unknown == 4, "an unmodelled status must be visible, not dropped"

    async def test_sensor_buckets_sum_to_total(self):
        brief = await _svc(
            sensor_repo=_FakeSensorRepo({"up": 5, "down": 1, "odd": 2}, (1, 3, 2))
        ).command_center_brief(uuid.uuid4())

        s = brief.infrastructure_health.sensors
        assert s.total == 8
        assert s.healthy + s.warning + s.critical + s.down + s.unknown == s.total
        assert s.stale == 2

    async def test_source_buckets_sum_to_total_sources(self):
        sources = [_Source(uuid.uuid4()) for _ in range(3)]
        brief = await _svc(
            source_repo=_FakeSourceRepo(sources)
        ).command_center_brief(uuid.uuid4())

        src = brief.infrastructure_health.sources
        assert src.total == 3
        assert src.healthy + src.degraded + src.failed + src.unknown == src.total


class TestDegradedIsNotUnknown:
    async def test_degraded_collection_gets_its_own_bucket(self):
        """A source that delivered SOME runs is not an unclassifiable one.

        Folding DEGRADED into UNKNOWN hides partially-broken collection behind an
        unexplained count - the exact merging this module forbids elsewhere.
        """
        source_id = uuid.uuid4()
        brief = await _svc(
            source_repo=_FakeSourceRepo([_Source(source_id)]),
            run_repo=_FakeRunRepo(stats={source_id: _stats(runs=2, succeeded=1)}),
        ).command_center_brief(uuid.uuid4())

        collectors = brief.infrastructure_health.collectors
        assert collectors.degraded == 1
        assert collectors.unknown == 0
        assert brief.infrastructure_health.sources.degraded == 1

        row = brief.collection_health.sources[0]
        assert row.collection_status == "DEGRADED"

    async def test_collector_buckets_sum_to_configured_total(self):
        source_id = uuid.uuid4()
        brief = await _svc(
            source_repo=_FakeSourceRepo([_Source(source_id)]),
            run_repo=_FakeRunRepo(stats={source_id: _stats(runs=2, succeeded=2)}),
        ).command_center_brief(uuid.uuid4())

        c = brief.infrastructure_health.collectors
        assert c.total == 1
        assert c.healthy + c.degraded + c.failed + c.stale + c.unknown == c.total

    async def test_inactive_source_is_not_configured(self):
        brief = await _svc(
            source_repo=_FakeSourceRepo([_Source(uuid.uuid4(), is_active=False)])
        ).command_center_brief(uuid.uuid4())

        assert brief.infrastructure_health.collectors.total == 0
        assert brief.collection_health.sources == []
        # Still counted as a source the tenant owns, just not a collecting one.
        assert brief.infrastructure_health.sources.total == 1


class TestPercentageScale:
    async def test_success_rate_is_on_a_zero_to_hundred_scale(self):
        """The backend already returns 0-100; multiplying again rendered 9850%."""
        source_id = uuid.uuid4()
        brief = await _svc(
            source_repo=_FakeSourceRepo([_Source(source_id)]),
            run_repo=_FakeRunRepo(stats={source_id: _stats(runs=2, succeeded=1)}),
        ).command_center_brief(uuid.uuid4())

        rate = brief.collection_health.sources[0].success_rate_pct
        assert rate == 50.0
        assert 0.0 <= rate <= 100.0


class TestTruthfulAbsence:
    async def test_empty_tenant_reports_null_rates_not_zero_or_hundred(self):
        """The rule most worth protecting: absence is None, never a fake number."""
        brief = await _svc().command_center_brief(uuid.uuid4())

        assert brief.kpis.availability_pct is None
        assert brief.kpis.mttr_minutes is None
        assert brief.kpis.mttd_minutes is None
        assert brief.kpis.availability_pct not in (0.0, 100.0)

    async def test_empty_tenant_declares_its_gaps(self):
        """An estate with nothing configured must say so, not look healthy."""
        brief = await _svc().command_center_brief(uuid.uuid4())

        gap_types = {gap.type for gap in brief.attention.gaps}
        assert "discovery_failure" in gap_types
        assert "inventory_incomplete" in gap_types
        assert brief.attention.items == []
        assert brief.attention.total == 0

    async def test_trends_report_unavailable_rather_than_empty_success(self):
        brief = await _svc().command_center_brief(uuid.uuid4())

        assert brief.trends.alerts.available is False
        assert brief.trends.alerts.buckets == []


class TestCollectionVsInfrastructure:
    async def test_the_two_failure_counts_are_reported_separately(self):
        """`distinction` exists so the UI cannot merge the two meanings."""
        source_id = uuid.uuid4()
        brief = await _svc(
            source_repo=_FakeSourceRepo([_Source(source_id)]),
            run_repo=_FakeRunRepo(
                statuses={"error": 3}, stats={source_id: _stats(runs=2, failed=1)}
            ),
        ).command_center_brief(uuid.uuid4())

        ch = brief.collection_health
        assert ch.collection_failure_count == 3
        assert ch.infrastructure_failure_count == 1
        assert ch.distinction
        assert ch.sources[0].collection_status == "FAILED"


class TestTrendBuckets:
    async def test_buckets_are_sorted_and_available_when_data_exists(self):
        later = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        earlier = later - timedelta(hours=1)

        class _AlertRepo(_FakeAlertRepo):
            async def hourly_counts(self, tenant_id, **kw):
                return {later: 4, earlier: 2}

        brief = await _svc(alert_repo=_AlertRepo()).command_center_brief(uuid.uuid4())

        alerts = brief.trends.alerts
        assert alerts.available is True
        assert [b.count for b in alerts.buckets] == [2, 4], "buckets must be time-ordered"
        assert [b.ts for b in alerts.buckets] == [earlier, later]

