"""
PHASE 6A - Operational Intelligence aggregation.

Two layers, deliberately:

* Pure-python units with fake repositories. No database, so these cover the
  arithmetic and the honesty rules - the parts most likely to break silently
  and most expensive to notice in production ("0% coverage" rendered for a
  tenant that has nothing configured).
* Real-Postgres integration tests for tenant scoping, following the existing
  suite convention (real Tenant row, cascade delete at teardown).

The query-budget tests are the N+1 guard: they assert the statement count is
FIXED, so the guarantee cannot regress as tenants grow.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.operational_intelligence_service import (
    FRESHNESS_WINDOW_SECONDS,
    OperationalIntelligenceService,
)


class _Source:
    def __init__(self, source_id: uuid.UUID, is_active: bool = True):
        self.id = source_id
        self.is_active = is_active


class _FakeSourceRepo:
    def __init__(self, sources: list):
        self.sources = sources
        self.calls = 0

    async def list(self, tenant_id, **kw):
        self.calls += 1
        return self.sources


class _FakeRunRepo:
    def __init__(self, statuses: dict, success_ids: set):
        self.statuses = statuses
        self.success_ids = success_ids
        self.calls = 0

    async def counts_by_status(self, tenant_id, **kw):
        self.calls += 1
        return self.statuses

    async def success_source_ids(self, tenant_id, **kw):
        self.calls += 1
        return self.success_ids


class _FakeSensorRepo:
    def __init__(self, statuses: dict, freshness):
        self.statuses = statuses
        self.freshness = freshness
        self.calls = 0

    async def counts_by_status(self, tenant_id, **kw):
        self.calls += 1
        return self.statuses

    async def counts_by_freshness(self, tenant_id, cutoff):
        self.calls += 1
        return self.freshness


class _FakeDeviceRepo:
    def __init__(self, statuses: dict):
        self.statuses = statuses
        self.calls = 0

    async def counts_by_status(self, tenant_id, **kw):
        self.calls += 1
        return self.statuses


class _FakeAlertRepo:
    def __init__(self, last_24h: int = 0, unack: int = 0, auto: int = 0):
        self.last_24h = last_24h
        self.unack = unack
        self.auto = auto
        self.calls = 0

    async def count(self, tenant_id, **kw):
        self.calls += 1
        if kw.get("auto_resolved"):
            return self.auto
        if kw.get("acknowledged") is False:
            return self.unack
        return self.last_24h


class _FakeTaskRepo:
    def __init__(self, statuses: dict):
        self.statuses = statuses
        self.calls = 0

    async def count_by_status(self, tenant_id, **kw):
        self.calls += 1
        return self.statuses


class _FakeIncidentRepo:
    """`snapshot` never touches incidents; the constructor now requires one."""

    def __init__(self):
        self.calls = 0

    async def list(self, tenant_id, **kw):
        self.calls += 1
        return []

    async def count(self, tenant_id, **kw):
        self.calls += 1
        return 0

    async def hourly_counts(self, tenant_id, **kw):
        self.calls += 1
        return {}

    async def mean_resolution_minutes(self, tenant_id, **kw):
        self.calls += 1
        return None


def _svc(**overrides) -> OperationalIntelligenceService:
    """Service over fakes, all empty by default."""
    repos = {
        "source_repo": _FakeSourceRepo([]),
        "run_repo": _FakeRunRepo({}, set()),
        "sensor_repo": _FakeSensorRepo({}, (0, 0, 0)),
        "device_repo": _FakeDeviceRepo({}),
        "alert_repo": _FakeAlertRepo(),
        "task_repo": _FakeTaskRepo({}),
        "incident_repo": _FakeIncidentRepo(),
    }
    repos.update(overrides)
    return OperationalIntelligenceService(**repos)


def _repos(source_count: int):
    """A full set of fakes whose call counters expose the query budget."""
    return (
        _FakeSourceRepo([_Source(uuid.uuid4()) for _ in range(source_count)]),
        _FakeRunRepo({"success": 1}, set()),
        _FakeSensorRepo({"up": 1}, (0, 1, 0)),
        _FakeDeviceRepo({"up": 1}),
        _FakeAlertRepo(),
        _FakeTaskRepo({}),
        _FakeIncidentRepo(),
    )


def _run(repos) -> None:
    """Drive the service over `repos`, so each fake counts its calls."""
    r = repos
    return OperationalIntelligenceService(
        source_repo=r[0], run_repo=r[1], sensor_repo=r[2],
        device_repo=r[3], alert_repo=r[4], task_repo=r[5],
        incident_repo=r[6],
    )# --------------------------------------------------------------------------- #
# Truthful absence - the rule most worth protecting                          #
# --------------------------------------------------------------------------- #
class TestTruthfulAbsence:
    async def test_empty_tenant_reports_null_rates_not_zero(self):
        """A tenant with no data must get nulls, NOT 0% and NOT 100%.

        The single most important assertion in the file: rendering "0%
        collection health" for a tenant that has simply never collected turns
        "no information" into "total outage", and 100% would be worse still.
        """
        snap = await _svc().snapshot(uuid.uuid4())

        assert snap.collection.runs_24h == 0
        assert snap.collection.health_percent is None
        assert snap.collection.coverage_configured == 0
        assert snap.collection.coverage_percent is None
        assert snap.freshness.total == 0
        assert snap.freshness.fresh_percent is None
        assert snap.ai.total == 0

        # Explicitly not 0.0/100.0 - guards a future "default to 0" edit.
        assert snap.collection.health_percent not in (0.0, 100.0)
        assert snap.collection.coverage_percent not in (0.0, 100.0)

    async def test_every_count_is_zero_for_empty_tenant(self):
        snap = await _svc().snapshot(uuid.uuid4())
        assert (snap.sources.active, snap.sources.total) == (0, 0)
        assert (snap.sensors.total, snap.devices.total) == (0, 0)
        assert (snap.alerts.last_24h, snap.alerts.unacknowledged) == (0, 0)

    async def test_all_failed_runs_really_is_zero_percent(self):
        """The flip side: when a denominator EXISTS, 0% is the honest answer."""
        snap = await _svc(
            run_repo=_FakeRunRepo({"error": 4}, set())
        ).snapshot(uuid.uuid4())
        assert snap.collection.runs_24h == 4
        assert snap.collection.health_percent == 0.0

    async def test_zero_fresh_sensors_is_null_not_100(self):
        """Every sensor fresh is 100%; ZERO sensors must never be."""
        snap = await _svc().snapshot(uuid.uuid4())
        assert snap.freshness.fresh_percent is None


# --------------------------------------------------------------------------- #
# Collection health + coverage                                               #
# --------------------------------------------------------------------------- #
class TestCollection:
    async def test_partial_counts_as_success_per_phase31_contract(self):
        """`partial` is a success for health (PHASE 3.1) but stays visible."""
        snap = await _svc(
            run_repo=_FakeRunRepo({"success": 2, "partial": 1, "error": 1}, set())
        ).snapshot(uuid.uuid4())

        assert snap.collection.runs_24h == 4
        assert snap.collection.succeeded_24h == 3  # success + partial
        assert snap.collection.partial_24h == 1  # not flattened away
        assert snap.collection.failed_24h == 1
        assert snap.collection.health_percent == 75.0

    async def test_coverage_counts_only_active_sources_that_collected(self):
        collected, never, disabled = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        snap = await _svc(
            source_repo=_FakeSourceRepo([
                _Source(collected, is_active=True),
                _Source(never, is_active=True),
                _Source(disabled, is_active=False),
            ]),
            run_repo=_FakeRunRepo({"success": 1}, {collected}),
        ).snapshot(uuid.uuid4())

        # The disabled source is excluded from the denominator entirely.
        assert (snap.sources.total, snap.sources.active) == (3, 2)
        assert snap.collection.coverage_configured == 2
        assert snap.collection.coverage_collected == 1
        assert snap.collection.coverage_percent == 50.0

    async def test_coverage_never_exceeds_configured(self):
        """A run for a DISABLED source must not inflate coverage past 100%."""
        active, disabled = uuid.uuid4(), uuid.uuid4()
        snap = await _svc(
            source_repo=_FakeSourceRepo([
                _Source(active, is_active=True),
                _Source(disabled, is_active=False),
            ]),
            run_repo=_FakeRunRepo({"success": 2}, {active, disabled}),
        ).snapshot(uuid.uuid4())
        assert snap.collection.coverage_percent == 100.0
        assert snap.collection.coverage_collected == 1# --------------------------------------------------------------------------- #
# Sensor / device / freshness buckets                                       #
# --------------------------------------------------------------------------- #
class TestBuckets:
    async def test_sensor_buckets_match_phase5c_vocabulary(self):
        snap = await _svc(
            sensor_repo=_FakeSensorRepo(
                {"up": 5, "down": 2, "warning": 1, "unknown": 3}, (0, 5, 3)
            )
        ).snapshot(uuid.uuid4())
        s = snap.sensors
        assert (s.healthy, s.down, s.warning, s.unknown, s.total) == (5, 2, 1, 3, 11)

    async def test_unmodelled_sensor_status_lands_in_unknown_not_dropped(self):
        """PHASE 5C defined four values; anything else stays visible."""
        snap = await _svc(
            sensor_repo=_FakeSensorRepo({"up": 2, "weird_new_state": 1}, (0, 2, 1))
        ).snapshot(uuid.uuid4())
        assert snap.sensors.unknown == 1
        assert snap.sensors.total == 3

    async def test_device_offline_and_degraded_are_separate_buckets(self):
        snap = await _svc(
            device_repo=_FakeDeviceRepo(
                {"up": 4, "down": 2, "degraded": 3, "unknown": 1}
            )
        ).snapshot(uuid.uuid4())
        d = snap.devices
        assert (d.online, d.offline, d.degraded, d.unknown, d.total) == (4, 2, 3, 1, 10)

    async def test_freshness_buckets_sum_to_total(self):
        snap = await _svc(
            sensor_repo=_FakeSensorRepo({}, (3, 10, 2))
        ).snapshot(uuid.uuid4())
        f = snap.freshness
        assert f.never_collected + f.fresh + f.stale == f.total == 15
        assert f.fresh_percent == pytest.approx(66.7, abs=0.1)

    async def test_never_collected_is_not_counted_as_stale_or_fresh(self):
        """A sensor with no reading is neither fresh nor stale.

        It gets its own bucket. Counting it as stale would invent a failure
        that never happened; counting it as fresh would invent a success. The
        rate IS a legitimate 0.0 here: 5 sensors exist and 0 are fresh, so the
        denominator is real - unlike the zero-denominator cases, which are null.
        """
        snap = await _svc(
            sensor_repo=_FakeSensorRepo({}, (5, 0, 0))
        ).snapshot(uuid.uuid4())
        assert snap.freshness.never_collected == 5
        assert snap.freshness.stale == 0
        assert snap.freshness.fresh == 0
        assert snap.freshness.total == 5
        assert snap.freshness.fresh_percent == 0.0

    async def test_stale_threshold_is_returned_for_the_client_to_state(self):
        snap = await _svc().snapshot(uuid.uuid4())
        assert snap.freshness.stale_after_seconds == FRESHNESS_WINDOW_SECONDS


# --------------------------------------------------------------------------- #
# AI task buckets                                                            #
# --------------------------------------------------------------------------- #
class TestAiTasks:
    async def test_buckets_sum_to_total_across_full_lifecycle(self):
        statuses = {
            "created": 2, "assigned": 1, "accepted": 1, "investigating": 1,
            "executing": 1, "verifying": 1, "verified": 1, "recorded": 1,
            "failed": 2, "retrying": 1, "escalated": 1, "cancelled": 3,
        }
        ai = (await _svc(task_repo=_FakeTaskRepo(statuses)).snapshot(uuid.uuid4())).ai
        assert ai.total == sum(statuses.values()) == 16
        assert ai.queued == 3
        assert ai.completed == 2  # verified + recorded
        assert ai.failed == 2
        assert ai.cancelled == 3
        # Nothing vanished: the buckets reconcile against the total.
        assert ai.queued + ai.running + ai.completed + ai.failed + ai.cancelled == ai.total

    async def test_unknown_task_status_is_kept_visible_in_running(self):
        ai = (
            await _svc(task_repo=_FakeTaskRepo({"brand_new": 2})).snapshot(uuid.uuid4())
        ).ai
        assert ai.total == 2
        assert ai.running == 2  # visible, not silently dropped# --------------------------------------------------------------------------- #
# The N+1 / query-budget guarantee                                          #
# --------------------------------------------------------------------------- #
class TestQueryBudget:
    async def test_query_count_is_fixed_and_small(self):
        """Exactly 10 aggregate calls, even for a 250-source tenant.

        The regression guard: a future edit adding a per-source or per-device
        query fails here loudly instead of quietly turning the dashboard into a
        500-query storm on a large tenant.
        """
        repos = _repos(250)
        await _run(repos).snapshot(uuid.uuid4())
        assert sum(r.calls for r in repos) == 10

    async def test_query_count_does_not_grow_with_row_count(self):
        """The budget is identical for 1 source and for 250 - no N+1 scaling."""
        small, big = _repos(1), _repos(250)
        await _run(small).snapshot(uuid.uuid4())
        await _run(big).snapshot(uuid.uuid4())
        assert sum(r.calls for r in small) == sum(r.calls for r in big) == 10


# --------------------------------------------------------------------------- #
# Serialization contract                                                     #
# --------------------------------------------------------------------------- #
class TestSerialization:
    async def test_payload_serializes_and_null_rates_survive(self):
        """nulls must survive JSON - the UI keys NO DATA off exactly this."""
        from app.schemas.dashboard import OperationalIntelligence

        snap = await _svc().snapshot(uuid.uuid4())
        data = snap.model_dump(mode="json")

        assert data["collection"]["health_percent"] is None
        assert data["collection"]["coverage_percent"] is None
        assert data["freshness"]["fresh_percent"] is None
        assert data["generated_at"] is not None
        assert set(data) == {
            "generated_at", "window_hours", "sources", "collection",
            "freshness", "sensors", "devices", "alerts", "ai",
        }
        # Round-trips without loss.
        assert OperationalIntelligence.model_validate(data) == snap

    async def test_window_hours_is_reported_once_and_consistently(self):
        snap = await _svc().snapshot(uuid.uuid4())
        assert snap.window_hours == 24
        assert snap.collection.window_hours == snap.window_hours

    async def test_generated_at_is_timezone_aware(self):
        snap = await _svc().snapshot(uuid.uuid4())
        assert snap.generated_at.tzinfo is not None


# --------------------------------------------------------------------------- #
# Integration (real Postgres) - tenant scoping                              #
# --------------------------------------------------------------------------- #
def _service_for(db):
    """Real repositories over one session."""
    from app.repositories.alert_repository import AlertRepository
    from app.repositories.collector_run_repository import CollectorRunRepository
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.incident_repository import IncidentRepository
    from app.repositories.monitoring_source_repository import (
        MonitoringSourceRepository,
    )
    from app.repositories.sensor_repository import SensorRepository
    from app.repositories.task_repository import TaskRepository

    return OperationalIntelligenceService(
        source_repo=MonitoringSourceRepository(db),
        run_repo=CollectorRunRepository(db),
        sensor_repo=SensorRepository(db),
        device_repo=DeviceRepository(db),
        alert_repo=AlertRepository(db),
        task_repo=TaskRepository(db),
        incident_repo=IncidentRepository(db),
    )


class TestTenantIsolation:
    """Real-Postgres proof that tenant scope is applied in SQL, per query.

    These carry the headline PHASE 6A safety property. They were previously
    indented INSIDE `_service_for()` below its `return`, so they never ran and
    the property was entirely unverified - and `Sensor.external_id` is NOT
    NULL, which the seeding omitted, so they would have errored had they
    executed at all. They are real test functions now.

    No new tenant mechanism is introduced: the service is built from the same
    production repositories over a real session that the dashboard route uses,
    so what is proven here is what ships.
    """

    async def test_snapshot_excludes_other_tenants_rows(self):
        """Seed two tenants with DISTINCT counts; each sees only its own.

        The headline PHASE 6A safety property: tenant scope is applied in SQL
        per query, never by post-filtering in Python.
        """
        from datetime import datetime, timezone

        from sqlalchemy import delete

        from app.core.db import AsyncSessionLocal
        from app.models.alert import Alert
        from app.models.device import Device
        from app.models.monitoring_source import MonitoringSource
        from app.models.sensor import Sensor
        from app.models.tenant import Tenant

        now = datetime.now(timezone.utc)
        tenants = []
        try:
            async with AsyncSessionLocal() as db:
                for n in (1, 2):
                    t = Tenant(name=f"OI {n}", slug=f"oi-{uuid.uuid4().hex[:10]}")
                    db.add(t)
                    await db.flush()
                    tenants.append(t)
                await db.commit()

            # Sensors require a non-null monitoring_source_id, so give each
            # tenant a real source first.
            source_ids: dict = {}
            async with AsyncSessionLocal() as db:
                for t in tenants:
                    src = MonitoringSource(
                        tenant_id=t.id, name="src", source_type="prtg",
                        is_active=True,
                    )
                    db.add(src)
                    await db.flush()
                    source_ids[t.id] = src.id
                await db.commit()

            async with AsyncSessionLocal() as db:
                for idx, t in enumerate(tenants):
                    # Tenant 1 gets 2 sensors, tenant 2 gets 1 -> distinguishable.
                    for i in range(2 if idx == 0 else 1):
                        db.add(Sensor(
                            tenant_id=t.id, name=f"s{i}", metric_type="uptime",
                            status="up", last_value_at=now,
                            external_id=f"{t.id}-{i}",
                            monitoring_source_id=source_ids[t.id],
                        ))
                    db.add(Device(
                        tenant_id=t.id, name="d0", device_type="switch",
                        status="up",
                    ))
                    db.add(Alert(
                        tenant_id=t.id, title="a", severity="critical",
                        status="open", triggered_at=now, last_occurred_at=now,
                    ))
                await db.commit()

            async with AsyncSessionLocal() as db:
                snap = await _service_for(db).snapshot(tenants[0].id)
            assert snap.sensors.total == 2, "saw another tenant's sensors"
            assert snap.devices.total == 1
            assert snap.alerts.last_24h == 1

            async with AsyncSessionLocal() as db:
                other = await _service_for(db).snapshot(tenants[1].id)
            assert other.sensors.total == 1, "tenant scoping is not applied"
            assert other.sensors.total != snap.sensors.total
        finally:
            async with AsyncSessionLocal() as db:
                for t in tenants:
                    await db.execute(delete(Tenant).where(Tenant.id == t.id))
                await db.commit()


    async def test_brief_excludes_other_tenants_rows(self):
        """`command_center_brief` must not surface ANY other tenant's row.

        Covers the categories `snapshot` does not carry - monitoring sources,
        collector runs, incidents and the response KPIs - alongside the ones it
        does. Checks IDENTITY, not merely counts: no id belonging to tenant B
        may appear anywhere in tenant A's brief. Counts alone could coincidentally
        agree; ids cannot.

        Tenant A is seeded with strictly MORE of every category than tenant B,
        so a leak in either direction moves a number.
        """
        from datetime import datetime, timezone

        from sqlalchemy import delete

        from app.core.db import AsyncSessionLocal
        from app.models.alert import Alert
        from app.models.collector_run import CollectorRun
        from app.models.device import Device
        from app.models.incident import Incident
        from app.models.monitoring_source import MonitoringSource
        from app.models.sensor import Sensor
        from app.models.tenant import Tenant

        now = datetime.now(timezone.utc)
        tenants: list = []
        incident_ids: dict = {}
        source_ids: dict = {}
        try:
            async with AsyncSessionLocal() as db:
                for n in (1, 2):
                    t = Tenant(
                        name=f"OI Brief {n}", slug=f"oib-{uuid.uuid4().hex[:10]}"
                    )
                    db.add(t)
                    await db.flush()
                    tenants.append(t)
                await db.commit()

            # Tenant A gets 2 sources, tenant B gets 1 -> a leaked source moves
            # `sources.total` and adds a row to collection_health.sources.
            async with AsyncSessionLocal() as db:
                for idx, t in enumerate(tenants):
                    for i in range(2 if idx == 0 else 1):
                        src = MonitoringSource(
                            tenant_id=t.id, name=f"src-{i}", source_type="prtg",
                            is_active=True, poll_interval_seconds=60,
                            status="pending",
                        )
                        db.add(src)
                        await db.flush()
                        source_ids.setdefault(t.id, []).append(src.id)
                await db.commit()

            async with AsyncSessionLocal() as db:
                for idx, t in enumerate(tenants):
                    for i in range(2 if idx == 0 else 1):
                        db.add(Sensor(
                            tenant_id=t.id, name=f"s{i}", metric_type="uptime",
                            status="up", last_value_at=now,
                            external_id=f"{t.id}-{i}",
                            monitoring_source_id=source_ids[t.id][0],
                        ))
                        db.add(Device(
                            tenant_id=t.id, name=f"d{i}", device_type="switch",
                            status="up",
                            monitoring_source_id=source_ids[t.id][0],
                        ))
                        db.add(Alert(
                            tenant_id=t.id, title=f"a{i}", severity="critical",
                            status="open", triggered_at=now, last_occurred_at=now,
                        ))
                    # Tenant A: one SUCCESS and one ERROR run. Tenant B: success
                    # only. Proves failed_24h reads CollectorRun.status == "error"
                    # AND that B's runs stay out of A's failure count.
                    specs = (
                        [("success", 0), ("error", 1)] if idx == 0
                        else [("success", 0)]
                    )
                    for status, src_idx in specs:
                        db.add(CollectorRun(
                            tenant_id=t.id,
                            monitoring_source_id=source_ids[t.id][src_idx],
                            trigger="scheduler", status=status,
                            started_at=now, finished_at=now,
                        ))
                    # Tenant A: 2 open incidents (1 critical). Tenant B: 1.
                    for i in range(2 if idx == 0 else 1):
                        inc = Incident(
                            tenant_id=t.id, title=f"inc-{t.id}-{i}",
                            severity="critical" if i == 0 else "warning",
                            status="open", opened_at=now, last_alert_at=now,
                        )
                        db.add(inc)
                        await db.flush()
                        incident_ids.setdefault(t.id, []).append(inc.id)
                await db.commit()

            async with AsyncSessionLocal() as db:
                brief = await _service_for(db).command_center_brief(tenants[0].id)
            async with AsyncSessionLocal() as db:
                other = await _service_for(db).command_center_brief(tenants[1].id)

            # --- inventory: devices / sensors / sources ----------------------
            assert brief.infrastructure_health.devices.total == 2, "saw B's devices"
            assert brief.infrastructure_health.sensors.total == 2, "saw B's sensors"
            assert brief.infrastructure_health.sources.total == 2, "saw B's sources"
            assert other.infrastructure_health.devices.total == 1
            assert other.infrastructure_health.sensors.total == 1
            assert other.infrastructure_health.sources.total == 1

            # --- collector runs: failure count is tenant-scoped --------------
            assert brief.collection_health.collection_failure_count == 1, (
                "failed_24h must see only this tenant's error runs"
            )
            assert other.collection_health.collection_failure_count == 0
            leaked_sources = {
                str(s) for s in source_ids[tenants[1].id]
            } & {row.source_id for row in brief.collection_health.sources}
            assert not leaked_sources, f"saw B's source rows: {leaked_sources}"

            # --- incidents: counts AND identity ------------------------------
            assert brief.kpis.open_incidents == 2, "saw B's incidents"
            assert brief.kpis.incident_volume_24h == 2
            assert brief.kpis.critical_open_incidents == 1
            assert brief.kpis.resolved_incidents_24h == 0
            assert other.kpis.open_incidents == 1
            assert other.kpis.open_incidents != brief.kpis.open_incidents

            leaked_incidents = {
                str(i) for i in incident_ids[tenants[1].id]
            } & {item.id for item in brief.attention.items}
            assert not leaked_incidents, f"saw B's incidents: {leaked_incidents}"

            # --- honest absence survives real data ---------------------------
            # Nothing was resolved, so MTTR has no sample set: None, not 0.
            assert brief.kpis.mttr_minutes is None
            # 2 of 2 devices "up" -> exactly 100.0 on the 0-100 scale.
            assert brief.kpis.availability_pct == 100.0

            # --- trends are chronological and backend-derived ----------------
            for series in (
                brief.trends.alerts, brief.trends.incidents,
                brief.trends.collection_failures, brief.trends.devices_down,
                brief.trends.stale_telemetry,
            ):
                stamps = [b.ts for b in series.buckets]
                assert stamps == sorted(stamps), "trend buckets not chronological"
                assert all(b.count >= 0 for b in series.buckets)
        finally:
            async with AsyncSessionLocal() as db:
                for t in tenants:
                    await db.execute(delete(Tenant).where(Tenant.id == t.id))
                await db.commit()


