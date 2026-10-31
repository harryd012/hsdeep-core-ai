"""Cloud Operations KPI - SQL-level tenant isolation & state-classification tests.

Real-Postgres integration tests (same convention as
test_operational_intelligence.py): a committed Tenant row (cascade-deleted at
teardown) plus Device/Incident rows, exercising the SAME repository methods the
endpoint uses so the SQL predicates themselves are validated - not just the
service arithmetic, which test_cloud_operations_brief.py already covers with
fakes.

These tests REQUIRE a reachable TEST_DATABASE_URL (see tests/conftest.py); they
never run against the dev database.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.device import Device
from app.models.incident import Incident
from app.models.tenant import Tenant
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.cloud_operations_service import CloudOperationsService

pytestmark = pytest.mark.asyncio


def _service(db) -> CloudOperationsService:
    return CloudOperationsService(
        device_repo=DeviceRepository(db),
        incident_repo=IncidentRepository(db),
    )


async def _add_tenant(name: str) -> Tenant:
    async with AsyncSessionLocal() as db:
        t = Tenant(name=name, slug=f"co-{uuid.uuid4().hex[:10]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    return t


async def _delete_tenant(t: Tenant) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


class TestTenantIsolationSQL:
    async def test_device_and_incident_counts_do_not_leak_across_tenants(self, tenant):
        now = datetime.now(timezone.utc)
        other = None
        try:
            other = await _add_tenant("CO other")
            async with AsyncSessionLocal() as db:
                db.add(Device(tenant_id=tenant.id, name="a-cr", device_type="cloud_resource", status="up", last_seen_at=now))
                db.add(Device(tenant_id=other.id, name="b-cr", device_type="cloud_resource", status="up", last_seen_at=now))
                db.add(Incident(tenant_id=tenant.id, title="a-open", severity="critical", status="open", opened_at=now, last_alert_at=now))
                db.add(Incident(tenant_id=other.id, title="b-open", severity="critical", status="open", opened_at=now, last_alert_at=now))
                await db.commit()

            async with AsyncSessionLocal() as db:
                snap = await _service(db).get(tenant.id)

            assert snap.cloud_resources.value == 1, "saw another tenant's device"
            assert snap.active_incidents.value == 1, "saw another tenant's incident"
        finally:
            if other is not None:
                await _delete_tenant(other)


class TestSoftDeleteExclusionSQL:
    async def test_soft_deleted_devices_and_incidents_are_excluded(self, tenant):
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            db.add(Device(tenant_id=tenant.id, name="live", device_type="cloud_resource", status="up", last_seen_at=now))
            db.add(Device(tenant_id=tenant.id, name="dead", device_type="cloud_resource", status="up", last_seen_at=now, is_deleted=True))
            db.add(Incident(tenant_id=tenant.id, title="live", severity="critical", status="open", opened_at=now, last_alert_at=now))
            db.add(Incident(tenant_id=tenant.id, title="dead", severity="critical", status="open", opened_at=now, last_alert_at=now, is_deleted=True))
            await db.commit()

        async with AsyncSessionLocal() as db:
            snap = await _service(db).get(tenant.id)

        assert snap.cloud_resources.value == 1
        assert snap.active_incidents.value == 1
        assert snap.resource_health.value["total"] == 1


class TestActiveStatusesSQL:
    async def test_only_documented_active_statuses_are_counted(self, tenant):
        now = datetime.now(timezone.utc)
        statuses = ["open", "acknowledged", "investigating", "resolved", "closed", "monitoring", "recovered"]
        async with AsyncSessionLocal() as db:
            for s in statuses:
                db.add(Incident(tenant_id=tenant.id, title=f"inc-{s}", severity="warning", status=s, opened_at=now, last_alert_at=now))
            await db.commit()

        async with AsyncSessionLocal() as db:
            snap = await _service(db).get(tenant.id)

        # Only open + acknowledged + investigating are active.
        assert snap.active_incidents.value == 3


class TestFreshnessClassificationSQL:
    async def test_null_last_seen_is_unknown_and_old_is_stale(self, tenant):
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            db.add(Device(tenant_id=tenant.id, name="fresh-up", device_type="cloud_resource", status="up", last_seen_at=now - timedelta(minutes=1)))
            db.add(Device(tenant_id=tenant.id, name="stale-up", device_type="cloud_resource", status="up", last_seen_at=now - timedelta(hours=25)))
            db.add(Device(tenant_id=tenant.id, name="never-seen", device_type="cloud_resource", status="up", last_seen_at=None))
            db.add(Device(tenant_id=tenant.id, name="fresh-down", device_type="cloud_resource", status="down", last_seen_at=now - timedelta(minutes=1)))
            await db.commit()

        async with AsyncSessionLocal() as db:
            snap = await _service(db).get(tenant.id)

        h = snap.resource_health.value
        assert h["online"] == 1    # fresh + up
        assert h["offline"] == 1   # fresh + down
        assert h["stale"] == 1     # 25h-old last_seen_at (beyond the 24h window)
        assert h["unknown"] == 1   # NULL last_seen_at
        assert h["total"] == 4


class TestUnavailableCardsSQL:
    async def test_unavailable_cards_never_become_zero_or_healthy(self, tenant):
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            db.add(Device(tenant_id=tenant.id, name="cr", device_type="cloud_resource", status="up", last_seen_at=now))
            await db.commit()

        async with AsyncSessionLocal() as db:
            snap = await _service(db).get(tenant.id)

        for card in (snap.cloud_spend, snap.policy_compliance, snap.backup_readiness):
            assert card.unavailable is True
            assert card.value is None
            assert card.unavailable_reason

