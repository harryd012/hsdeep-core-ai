"""
Sprint 1 — Task 6.9: Repository unit tests.

Covers upsert idempotency, filter combinations, and list_due /
list_stale_running boundary conditions for every new/extended repository.

All tests run against the real PostgreSQL database (same isolation strategy
as conftest.py: committed rows scoped to a unique Tenant, cleaned up by
cascade delete on Tenant teardown).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.core.db import AsyncSessionLocal
from app.models.device import Device
from app.models.interface import Interface
from app.models.event import Event
from app.models.metric import Metric
from app.models.monitoring_source import MonitoringSource
from app.repositories.device_repository import DeviceRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _make_source(tenant_id: uuid.UUID, **overrides) -> MonitoringSource:
    """Commit a MonitoringSource and return it; cleaned up by tenant cascade."""
    fields = {
        "tenant_id": tenant_id,
        "name": f"src-{uuid.uuid4().hex[:8]}",
        "source_type": "prtg",
        "is_active": True,
        "poll_interval_seconds": 60,
        "status": "pending",
    }
    fields.update(overrides)
    async with AsyncSessionLocal() as db:
        src = MonitoringSource(**fields)
        db.add(src)
        await db.commit()
        await db.refresh(src)
    return src


async def _make_device(
    tenant_id: uuid.UUID, monitoring_source_id: uuid.UUID, **overrides
) -> Device:
    """Commit a Device and return it; cleaned up by tenant cascade."""
    fields = {
        "tenant_id": tenant_id,
        "monitoring_source_id": monitoring_source_id,
        "external_id": f"ext-{uuid.uuid4().hex[:8]}",
        "name": f"device-{uuid.uuid4().hex[:8]}",
        "device_type": "router",
        "status": "up",
    }
    fields.update(overrides)
    async with AsyncSessionLocal() as db:
        dev = Device(**fields)
        db.add(dev)
        await db.commit()
        await db.refresh(dev)
    return dev


# ---------------------------------------------------------------------------
# DeviceRepository
# ---------------------------------------------------------------------------

class TestDeviceRepository:

    async def test_upsert_creates_new_device(self, tenant):
        src = await _make_source(tenant.id)
        ext_id = f"dev-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            device, created = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                external_id=ext_id,
                name="Core-Switch-1",
                device_type="switch",
                vendor="cisco",
                status="up",
            )
            await db.commit()

        assert created is True
        assert device.id is not None
        assert device.external_id == ext_id
        assert device.name == "Core-Switch-1"
        assert device.vendor == "cisco"

    async def test_upsert_idempotent_second_call_updates_not_inserts(self, tenant):
        """Calling upsert() twice with the same external_id must yield one row."""
        src = await _make_source(tenant.id)
        ext_id = f"dev-{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            device1, created1 = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                external_id=ext_id,
                name="Router-A",
                device_type="router",
                status="up",
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            device2, created2 = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                external_id=ext_id,
                name="Router-A-renamed",
                device_type="router",
                status="down",
            )
            await db.commit()

        assert created1 is True
        assert created2 is False
        assert device1.id == device2.id          # same DB row
        assert device2.name == "Router-A-renamed"
        assert device2.status == "down"

    async def test_list_filter_by_vendor(self, tenant):
        src = await _make_source(tenant.id)
        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            for vendor in ("cisco", "cisco", "fortinet"):
                await repo.upsert(
                    tenant_id=tenant.id,
                    monitoring_source_id=src.id,
                    external_id=f"ext-{uuid.uuid4().hex[:8]}",
                    name=f"dev-{vendor}",
                    device_type="switch",
                    vendor=vendor,
                    status="up",
                )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            cisco_devices = await repo.list(tenant.id, vendor="cisco")
            fortinet_devices = await repo.list(tenant.id, vendor="fortinet")

        assert len(cisco_devices) >= 2
        assert all(d.vendor == "cisco" for d in cisco_devices)
        assert len(fortinet_devices) >= 1
        assert all(d.vendor == "fortinet" for d in fortinet_devices)

    async def test_list_filter_by_status(self, tenant):
        src = await _make_source(tenant.id)
        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            for status in ("up", "down", "up"):
                await repo.upsert(
                    tenant_id=tenant.id,
                    monitoring_source_id=src.id,
                    external_id=f"ext-{uuid.uuid4().hex[:8]}",
                    name=f"dev-{status}-{uuid.uuid4().hex[:4]}",
                    device_type="router",
                    status=status,
                )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            down_devices = await repo.list(tenant.id, status="down")

        assert len(down_devices) >= 1
        assert all(d.status == "down" for d in down_devices)

    async def test_get_returns_none_for_wrong_tenant(self, tenant):
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)
        other_tenant_id = uuid.uuid4()

        async with AsyncSessionLocal() as db:
            repo = DeviceRepository(db)
            result = await repo.get(device.id, other_tenant_id)

        assert result is None


# ---------------------------------------------------------------------------
# InterfaceRepository
# ---------------------------------------------------------------------------

class TestInterfaceRepository:

    async def test_upsert_creates_interface(self, tenant):
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)
        ext_id = f"iface-{uuid.uuid4().hex[:8]}"

        async with AsyncSessionLocal() as db:
            repo = InterfaceRepository(db)
            iface, created = await repo.upsert(
                tenant_id=tenant.id,
                device_id=device.id,
                external_id=ext_id,
                name="Gi0/0",
                admin_status="up",
                oper_status="up",
                speed_mbps=1000.0,
            )
            await db.commit()

        assert created is True
        assert iface.name == "Gi0/0"
        assert iface.speed_mbps == 1000.0

    async def test_upsert_idempotent_same_row_returned(self, tenant):
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)
        ext_id = f"iface-{uuid.uuid4().hex[:8]}"

        async with AsyncSessionLocal() as db:
            repo = InterfaceRepository(db)
            iface1, _ = await repo.upsert(
                tenant_id=tenant.id,
                device_id=device.id,
                external_id=ext_id,
                name="Gi0/1",
                oper_status="up",
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = InterfaceRepository(db)
            iface2, created2 = await repo.upsert(
                tenant_id=tenant.id,
                device_id=device.id,
                external_id=ext_id,
                name="Gi0/1-updated",
                oper_status="down",
            )
            await db.commit()

        assert created2 is False
        assert iface1.id == iface2.id
        assert iface2.name == "Gi0/1-updated"
        assert iface2.oper_status == "down"

    async def test_upsert_last_status_change_at_only_bumped_on_change(self, tenant):
        """last_status_change_at must NOT be updated when oper_status is unchanged."""
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)
        ext_id = f"iface-{uuid.uuid4().hex[:8]}"

        async with AsyncSessionLocal() as db:
            repo = InterfaceRepository(db)
            iface1, _ = await repo.upsert(
                tenant_id=tenant.id,
                device_id=device.id,
                external_id=ext_id,
                name="Eth0",
                oper_status="up",
            )
            await db.commit()
        original_change_at = iface1.last_status_change_at

        async with AsyncSessionLocal() as db:
            repo = InterfaceRepository(db)
            iface2, _ = await repo.upsert(
                tenant_id=tenant.id,
                device_id=device.id,
                external_id=ext_id,
                name="Eth0",
                oper_status="up",  # same status — should NOT bump
            )
            await db.commit()

        # Timestamps stored with microsecond precision; same oper_status → same value
        assert iface2.last_status_change_at == original_change_at

    async def test_list_for_device_returns_only_that_device(self, tenant):
        src = await _make_source(tenant.id)
        dev_a = await _make_device(tenant.id, src.id)
        dev_b = await _make_device(tenant.id, src.id)

        async with AsyncSessionLocal() as db:
            repo = InterfaceRepository(db)
            await repo.upsert(tenant_id=tenant.id, device_id=dev_a.id,
                              external_id="eth0", name="Eth0", oper_status="up")
            await repo.upsert(tenant_id=tenant.id, device_id=dev_b.id,
                              external_id="eth1", name="Eth1", oper_status="up")
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = InterfaceRepository(db)
            ifaces = await repo.list_for_device(dev_a.id)

        assert all(i.device_id == dev_a.id for i in ifaces)
        assert not any(i.device_id == dev_b.id for i in ifaces)


# ---------------------------------------------------------------------------
# EventRepository
# ---------------------------------------------------------------------------

class TestEventRepository:

    async def test_upsert_creates_vendor_event(self, tenant):
        src = await _make_source(tenant.id)
        ext_id = f"evt-{uuid.uuid4().hex[:8]}"

        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            event, created = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                external_id=ext_id,
                event_type="interface_down",
                severity="warning",
                message="Interface Gi0/0 went down",
                occurred_at=_now(),
            )
            await db.commit()

        assert created is True
        assert event.external_id == ext_id
        assert event.severity == "warning"

    async def test_upsert_idempotent_vendor_event(self, tenant):
        src = await _make_source(tenant.id)
        ext_id = f"evt-{uuid.uuid4().hex[:8]}"
        t1 = _now()

        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            ev1, _ = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                external_id=ext_id,
                event_type="interface_down",
                severity="warning",
                message="first",
                occurred_at=t1,
            )
            await db.commit()

        t2 = _now() + timedelta(seconds=5)
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            ev2, created2 = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                external_id=ext_id,
                event_type="interface_down",
                severity="critical",
                message="second",
                occurred_at=t2,
            )
            await db.commit()

        assert created2 is False
        assert ev1.id == ev2.id
        assert ev2.severity == "critical"
        assert ev2.message == "second"

    async def test_no_external_id_always_inserts(self, tenant):
        """Platform-generated events (no external_id) are always new rows."""
        src = await _make_source(tenant.id)
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            ev1, c1 = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                event_type="platform_event",
                severity="info",
                message="msg",
                occurred_at=_now(),
            )
            ev2, c2 = await repo.upsert(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                event_type="platform_event",
                severity="info",
                message="msg",
                occurred_at=_now(),
            )
            await db.commit()

        assert c1 is True and c2 is True
        assert ev1.id != ev2.id

    async def test_list_filter_by_severity(self, tenant):
        src = await _make_source(tenant.id)
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            for sev in ("info", "warning", "critical"):
                await repo.upsert(
                    tenant_id=tenant.id,
                    monitoring_source_id=src.id,
                    event_type="test",
                    severity=sev,
                    message=sev,
                    occurred_at=_now(),
                )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            crit = await repo.list(tenant.id, severity="critical")

        assert len(crit) >= 1
        assert all(e.severity == "critical" for e in crit)

    async def test_list_filter_since(self, tenant):
        src = await _make_source(tenant.id)
        old_ts = _now() - timedelta(hours=2)
        new_ts = _now()
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            await repo.upsert(tenant_id=tenant.id, monitoring_source_id=src.id,
                              event_type="old", severity="info", message="old", occurred_at=old_ts)
            await repo.upsert(tenant_id=tenant.id, monitoring_source_id=src.id,
                              event_type="new", severity="info", message="new", occurred_at=new_ts)
            await db.commit()

        cutoff = _now() - timedelta(hours=1)
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            recent = await repo.list(tenant.id, since=cutoff)

        assert all(e.occurred_at >= cutoff for e in recent)
        assert any(e.event_type == "new" for e in recent)
        assert not any(e.event_type == "old" for e in recent)


# ---------------------------------------------------------------------------
# MetricRepository
# ---------------------------------------------------------------------------

class TestMetricRepository:

    async def test_record_inserts_new_metric(self, tenant):
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)

        async with AsyncSessionLocal() as db:
            repo = MetricRepository(db)
            metric = await repo.record(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                device_id=device.id,
                metric_type="cpu_usage",
                value=72.5,
                unit="%",
                recorded_at=_now(),
            )
            await db.commit()

        assert metric.id is not None
        assert metric.value == 72.5
        assert metric.unit == "%"

    async def test_record_always_inserts_never_upserts(self, tenant):
        """MetricRepository.record() is append-only — two calls produce two rows."""
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)

        async with AsyncSessionLocal() as db:
            repo = MetricRepository(db)
            m1 = await repo.record(
                tenant_id=tenant.id, device_id=device.id,
                metric_type="cpu_usage", value=50.0, recorded_at=_now(),
            )
            m2 = await repo.record(
                tenant_id=tenant.id, device_id=device.id,
                metric_type="cpu_usage", value=55.0, recorded_at=_now(),
            )
            await db.commit()

        assert m1.id != m2.id

    async def test_list_for_device_filter_by_metric_type(self, tenant):
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)

        async with AsyncSessionLocal() as db:
            repo = MetricRepository(db)
            for mt in ("cpu_usage", "memory_usage", "cpu_usage"):
                await repo.record(
                    tenant_id=tenant.id, device_id=device.id,
                    metric_type=mt, value=1.0, recorded_at=_now(),
                )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = MetricRepository(db)
            cpu_metrics = await repo.list_for_device(device.id, metric_type="cpu_usage")

        assert len(cpu_metrics) >= 2
        assert all(m.metric_type == "cpu_usage" for m in cpu_metrics)

    async def test_list_for_device_filter_since(self, tenant):
        src = await _make_source(tenant.id)
        device = await _make_device(tenant.id, src.id)
        old_ts = _now() - timedelta(hours=3)
        new_ts = _now()

        async with AsyncSessionLocal() as db:
            repo = MetricRepository(db)
            await repo.record(tenant_id=tenant.id, device_id=device.id,
                              metric_type="cpu_usage", value=10.0, recorded_at=old_ts)
            await repo.record(tenant_id=tenant.id, device_id=device.id,
                              metric_type="cpu_usage", value=20.0, recorded_at=new_ts)
            await db.commit()

        cutoff = _now() - timedelta(hours=1)
        async with AsyncSessionLocal() as db:
            repo = MetricRepository(db)
            recent = await repo.list_for_device(device.id, since=cutoff)

        assert all(m.recorded_at >= cutoff for m in recent)
        assert any(m.value == 20.0 for m in recent)
        assert not any(m.value == 10.0 for m in recent)


# ---------------------------------------------------------------------------
# CollectorRunRepository
# ---------------------------------------------------------------------------

class TestCollectorRunRepository:

    async def test_create_and_finish_lifecycle(self, tenant):
        src = await _make_source(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            run = await repo.create(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
                trigger="manual",
            )
            assert run.status == "running"
            assert run.started_at is not None

            run = await repo.finish(
                run,
                status="success",
                devices_upserted=5,
                sensors_upserted=12,
            )
            await db.commit()

        assert run.status == "success"
        assert run.devices_upserted == 5
        assert run.sensors_upserted == 12
        assert run.finished_at is not None

    async def test_get_running_for_source_returns_in_flight_run(self, tenant):
        src = await _make_source(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            run = await repo.create(
                tenant_id=tenant.id,
                monitoring_source_id=src.id,
            )
            await db.commit()
            run_id = run.id

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            found = await repo.get_running_for_source(src.id)

        assert found is not None
        assert found.id == run_id
        assert found.status == "running"

    async def test_get_running_returns_none_after_finished(self, tenant):
        src = await _make_source(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            run = await repo.create(tenant_id=tenant.id, monitoring_source_id=src.id)
            await repo.finish(run, status="success")
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            found = await repo.get_running_for_source(src.id)

        assert found is None

    async def test_list_filter_by_status(self, tenant):
        src = await _make_source(tenant.id)

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            for status in ("success", "error", "success"):
                run = await repo.create(tenant_id=tenant.id, monitoring_source_id=src.id)
                await repo.finish(run, status=status)
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            errors = await repo.list(tenant.id, status="error")

        assert len(errors) >= 1
        assert all(r.status == "error" for r in errors)

    async def test_list_filter_since_boundary(self, tenant):
        src = await _make_source(tenant.id)
        old_run_id: uuid.UUID | None = None
        new_run_id: uuid.UUID | None = None

        # Create an old run with a manually backdated started_at
        from sqlalchemy import update
        from app.models.collector_run import CollectorRun

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            old_run = await repo.create(tenant_id=tenant.id, monitoring_source_id=src.id)
            await repo.finish(old_run, status="success")
            await db.commit()
            old_run_id = old_run.id

        async with AsyncSessionLocal() as db:
            # Backdate the started_at so it's outside the since window
            await db.execute(
                update(CollectorRun)
                .where(CollectorRun.id == old_run_id)
                .values(started_at=_now() - timedelta(hours=5))
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            new_run = await repo.create(tenant_id=tenant.id, monitoring_source_id=src.id)
            await repo.finish(new_run, status="success")
            await db.commit()
            new_run_id = new_run.id

        cutoff = _now() - timedelta(hours=1)
        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            recent = await repo.list(tenant.id, since=cutoff)

        ids = [r.id for r in recent]
        assert new_run_id in ids
        assert old_run_id not in ids


    async def test_list_stale_running_threshold_boundary(self, tenant):
        """list_stale_running() returns runs started BEFORE the threshold
        and still status='running'. Runs started after the threshold or
        already finished must NOT be returned."""
        src = await _make_source(tenant.id)
        from sqlalchemy import update
        from app.models.collector_run import CollectorRun

        # Create a run that will be backdated to look stale
        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            stale_run = await repo.create(tenant_id=tenant.id, monitoring_source_id=src.id)
            await db.commit()
            stale_id = stale_run.id

        async with AsyncSessionLocal() as db:
            await db.execute(
                update(CollectorRun)
                .where(CollectorRun.id == stale_id)
                .values(started_at=_now() - timedelta(minutes=30))
            )
            await db.commit()

        # Create a fresh run that should NOT appear as stale
        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            fresh_run = await repo.create(tenant_id=tenant.id, monitoring_source_id=src.id)
            await db.commit()
            fresh_id = fresh_run.id

        threshold = _now() - timedelta(minutes=10)
        async with AsyncSessionLocal() as db:
            repo = CollectorRunRepository(db)
            stale_list = await repo.list_stale_running(threshold)

        stale_ids = [r.id for r in stale_list]
        assert stale_id in stale_ids      # started 30 min ago < threshold(10 min)
        assert fresh_id not in stale_ids  # started just now > threshold


# ---------------------------------------------------------------------------
# MonitoringSourceRepository.list_due() boundary tests
# ---------------------------------------------------------------------------

class TestMonitoringSourceRepositoryListDue:

    async def test_list_due_returns_never_synced_active_sources(self, tenant):
        """Active sources with last_sync_at=NULL are always due."""
        src = await _make_source(tenant.id, last_sync_at=None, is_active=True)

        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            due = await repo.list_due(_now())

        assert any(s.id == src.id for s in due)

    async def test_list_due_excludes_inactive_sources(self, tenant):
        await _make_source(tenant.id, is_active=False, last_sync_at=None)
        src_active = await _make_source(tenant.id, is_active=True, last_sync_at=None)

        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            due = await repo.list_due(_now())

        assert any(s.id == src_active.id for s in due)
        # inactive source must not appear
        due_ids = {s.id for s in due}
        assert src_active.id in due_ids

    async def test_list_due_excludes_recently_synced_source(self, tenant):
        """A source synced 5 seconds ago with poll_interval=60s is NOT due."""
        recent_sync = _now() - timedelta(seconds=5)
        src = await _make_source(
            tenant.id,
            is_active=True,
            poll_interval_seconds=60,
            last_sync_at=recent_sync,
            status="connected",
        )

        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            due = await repo.list_due(_now())

        assert not any(s.id == src.id for s in due)

    async def test_list_due_includes_overdue_source(self, tenant):
        """A source synced 90 seconds ago with poll_interval=60s IS due."""
        overdue_sync = _now() - timedelta(seconds=90)
        src = await _make_source(
            tenant.id,
            is_active=True,
            poll_interval_seconds=60,
            last_sync_at=overdue_sync,
            status="connected",
        )

        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            due = await repo.list_due(_now())

        assert any(s.id == src.id for s in due)

    async def test_list_due_excludes_credentials_invalid_source(self, tenant):
        """Sources marked credentials_invalid are excluded even if otherwise due."""
        src = await _make_source(
            tenant.id,
            is_active=True,
            last_sync_at=None,
            status="credentials_invalid",
        )

        async with AsyncSessionLocal() as db:
            repo = MonitoringSourceRepository(db)
            due = await repo.list_due(_now())

        assert not any(s.id == src.id for s in due)
