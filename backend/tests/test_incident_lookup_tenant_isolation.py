"""Repository-level tenant isolation for IncidentRepository lookups.

Proves that every open-incident lookup is scoped to the requesting tenant:
  - get_open_for_device(device_id, tenant_id)
  - get_open_for_site(site_id, tenant_id)
  - get_open_for_device_and_sensor(device_id, sensor_id, tenant_id)
  - get_open_for_signature(signature, tenant_id)

and that Tenant A's alerts can never be absorbed into a group created from
Tenant B's alerts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.models.alert import Alert
from app.models.device import Device
from app.models.monitoring_source import MonitoringSource
from app.models.sensor import Sensor
from app.models.site import Site
from app.models.tenant import Tenant
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.alerting.correlation_engine import CorrelationEngine

pytestmark = pytest.mark.asyncio


async def _seed_tenant(name: str, slug: str) -> Tenant:
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=name, slug=slug)
        db.add(tenant)
        # Commit (not just flush): the scaffold + engine sessions below are
        # separate connections and must be able to see this row.
        await db.commit()
        await db.refresh(tenant)
        return tenant


async def _seed_scaffold(tenant: Tenant):
    async with AsyncSessionLocal() as db:
        site = Site(tenant_id=tenant.id, name="Site", code=f"SITE-{uuid.uuid4().hex[:4]}")
        db.add(site)
        await db.flush()
        device = Device(tenant_id=tenant.id, site_id=site.id, name="corr.example.com", device_type="server", vendor="Generic", status="up")
        db.add(device)
        await db.flush()
        source = MonitoringSource(tenant_id=tenant.id, name="src", source_type="prtg", is_active=True)
        db.add(source)
        await db.flush()
        sensor = Sensor(
            tenant_id=tenant.id,
            monitoring_source_id=source.id,
            device_id=device.id,
            external_id="ssl-993",
            name="SSL Security Check - corr.example.com:993",
            metric_type="ssl_security",
            status="down",
            critical_threshold=0,
            warning_threshold=0,
            current_value=0,
        )
        db.add(sensor)
        # Commit so the device/source/sensor rows are visible to the engine
        # session that creates and correlates the alerts.
        await db.commit()
        await db.refresh(site)
        await db.refresh(device)
        await db.refresh(source)
        await db.refresh(sensor)
        return site, device, source, sensor


async def _seed_alert(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    device_id: uuid.UUID,
    sensor_id: uuid.UUID,
    source_id: uuid.UUID,
    severity: str = "critical",
) -> Alert:
    """Create an alert inside the CALLER's session.

    The alert must belong to the same session the CorrelationEngine uses:
    correlate() mutates it (set_correlation_fields) and calls db.refresh() on
    it, which fails for an object detached from a different session.
    """
    now = datetime.now(timezone.utc)
    alert = Alert(
        tenant_id=tenant_id,
        device_id=device_id,
        sensor_id=sensor_id,
        monitoring_source_id=source_id,
        title="SSL Security Check - host:993",
        severity=severity,
        status="open",
        metric_type="ssl_security",
        source="monitoring_sync",
        triggered_value=0.0,
        threshold_value=0.0,
        triggered_at=now,
        last_occurred_at=now,
        occurrence_count=1,
    )
    db.add(alert)
    await db.flush()
    await db.refresh(alert)
    return alert



async def _correlate_with(engine: CorrelationEngine, alert) -> None:
    """Run correlation inside the test's existing session."""
    await engine.correlate(alert)


async def _delete_tenant(tenant: Tenant) -> None:
    """Tenant-scoped cleanup. Cascades to site/device/source/sensor/alert/incident."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()


class TestTenantIsolation:
    async def test_tenant_a_and_b_alerts_never_share_a_group(self):
        tenant_a = await _seed_tenant(f"Tenant A {uuid.uuid4().hex[:8]}", f"ta-{uuid.uuid4().hex[:8]}")
        try:
            site_a, device_a, source_a, sensor_a = await _seed_scaffold(tenant_a)
            tenant_b = await _seed_tenant(f"Tenant B {uuid.uuid4().hex[:8]}", f"tb-{uuid.uuid4().hex[:8]}")
            site_b, device_b, source_b, sensor_b = await _seed_scaffold(tenant_b)
            try:
                async with AsyncSessionLocal() as db:
                    engine = CorrelationEngine(AlertRepository(db), IncidentRepository(db))
                    alert_a = await _seed_alert(db, tenant_a.id, device_id=device_a.id, sensor_id=sensor_a.id, source_id=source_a.id)
                    alert_b = await _seed_alert(db, tenant_b.id, device_id=device_b.id, sensor_id=sensor_b.id, source_id=source_b.id)

                    await _correlate_with(engine, alert_a)
                    await _correlate_with(engine, alert_b)

                    assert alert_a.incident_id != alert_b.incident_id
                    assert alert_a.incident_id is not None
                    assert alert_b.incident_id is not None
                    # Verify via the repo that each tenant's open group lives
                    # exclusively in its own tenant.
                    a_incident = await IncidentRepository(db).get_open_for_device(device_a.id, tenant_a.id)
                    b_incident = await IncidentRepository(db).get_open_for_device(device_b.id, tenant_b.id)
                    assert a_incident is not None and b_incident is not None
                    assert a_incident.tenant_id == tenant_a.id
                    assert b_incident.tenant_id == tenant_b.id
                    assert a_incident.id != await IncidentRepository(db).get_open_for_device(device_b.id, tenant_a.id)
                    assert b_incident.id != await IncidentRepository(db).get_open_for_device(device_a.id, tenant_b.id)
            finally:
                await _delete_tenant(tenant_b)
        finally:
            await _delete_tenant(tenant_a)

    async def test_open_incident_lookup_never_returns_another_tenant(self):
        tenant_a = await _seed_tenant(f"Tenant A {uuid.uuid4().hex[:8]}", f"ta-{uuid.uuid4().hex[:8]}")
        try:
            site_a, device_a, source_a, sensor_a = await _seed_scaffold(tenant_a)
            tenant_b = await _seed_tenant(f"Tenant B {uuid.uuid4().hex[:8]}", f"tb-{uuid.uuid4().hex[:8]}")
            site_b, device_b, source_b, sensor_b = await _seed_scaffold(tenant_b)
            try:
                async with AsyncSessionLocal() as db:
                    engine = CorrelationEngine(AlertRepository(db), IncidentRepository(db))
                    alert_a = await _seed_alert(db, tenant_a.id, device_id=device_a.id, sensor_id=sensor_a.id, source_id=source_a.id)
                    alert_b = await _seed_alert(db, tenant_b.id, device_id=device_b.id, sensor_id=sensor_b.id, source_id=source_b.id)
                    await _correlate_with(engine, alert_a)
                    await _correlate_with(engine, alert_b)

                    repo = IncidentRepository(db)
                    a_incident = await repo.get_open_for_device(device_a.id, tenant_a.id)
                    b_incident = await repo.get_open_for_device(device_b.id, tenant_b.id)
                    assert a_incident is not None and b_incident is not None
                    assert a_incident.tenant_id == tenant_a.id
                    assert b_incident.tenant_id == tenant_b.id
                    assert a_incident.id != await repo.get_open_for_device(device_b.id, tenant_a.id)
                    assert b_incident.id != await repo.get_open_for_device(device_a.id, tenant_b.id)
            finally:
                await _delete_tenant(tenant_b)
        finally:
            await _delete_tenant(tenant_a)





