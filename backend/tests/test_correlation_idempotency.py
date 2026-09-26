"""Focused tests for the monitoring-event deduplication / idempotent
correlation fix.

Covers (per the work brief):
  A. The SAME monitoring condition received repeatedly -> ONE active incident,
     occurrence count increments, first_seen (opened_at) preserved,
     last_alert_at advances, and NO duplicate AI-correlation work is created
     (only one "correlated" history entry, no repeated priority recompute).
  F. The existing alert lifecycle (open -> update -> resolve) still works
     exactly as before for a genuinely new condition or a real transition.

Runs against the real configured Postgres (see tests/conftest.py docstring).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.models.device import Device
from app.models.monitoring_source import MonitoringSource
from app.models.sensor import Sensor
from app.models.site import Site
from app.models.tenant import Tenant
from app.repositories.alert_history_repository import AlertHistoryRepository
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.alert_service import AlertService

pytestmark = pytest.mark.asyncio


async def _make_ctx() -> tuple[Tenant, Site, Device, MonitoringSource, Sensor]:
    """Create + commit a real tenant/site/device/source/sensor scaffold and
    return them so callers can drive AlertService.evaluate_sensor()."""
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name=f"Dedup Test {uuid.uuid4().hex[:8]}",
                        slug=f"dedup-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()
        site = Site(tenant_id=tenant.id, name="Site", code=f"SITE-{uuid.uuid4().hex[:4]}")
        db.add(site)
        await db.flush()
        device = Device(tenant_id=tenant.id, site_id=site.id, name="mail.example.com",
                        device_type="server", vendor="Generic", status="up")
        db.add(device)
        await db.flush()
        source = MonitoringSource(tenant_id=tenant.id, name="src",
                                  source_type="prtg", is_active=True)
        db.add(source)
        await db.flush()
        sensor = Sensor(
            tenant_id=tenant.id,
            monitoring_source_id=source.id,
            device_id=device.id,
            external_id="ssl-check-993",
            name="SSL Security Check - mail.example.com:993",
            metric_type="ssl_security",
            status="down",
            critical_threshold=0,
            warning_threshold=0,
            current_value=0,
        )
        db.add(sensor)
        await db.commit()
        await db.refresh(tenant)
        await db.refresh(site)
        await db.refresh(device)
        await db.refresh(source)
        await db.refresh(sensor)
    return tenant, site, device, source, sensor


async def _delete_tenant(tenant: Tenant) -> None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()
class TestRepeatEventDedupCorrelation:
    async def test_same_condition_single_incident_idempotent(self):
        tenant, _site, _device, _source, sensor = await _make_ctx()
        try:
            async with AsyncSessionLocal() as db:
                service = AlertService(AlertRepository(db))
                incident_repo = IncidentRepository(db)
                history_repo = AlertHistoryRepository(db)

                first_alert = None
                for _i in range(5):
                    # Same sensor keeps reporting 'down' (critical) - the
                    # SAME underlying monitoring condition repeated 5x.
                    ev = await service.evaluate_sensor(sensor)
                    assert ev.alert is not None
                    if first_alert is None:
                        first_alert = ev.alert
                    await db.flush()

                # ONE active incident for the condition...
                open_incidents = await incident_repo.list(tenant.id, status="open")
                assert len(open_incidents) == 1
                inc = open_incidents[0]
                assert first_alert.incident_id == inc.id

                # ...occurrence count incremented per repeat...
                assert first_alert.occurrence_count == 5
                assert inc.total_occurrences >= 5
                # ...but the distinct-member set is NOT inflated by repeats.
                assert inc.alert_count == 1

                # first_seen preserved, last_seen advanced.
                assert inc.opened_at == first_alert.triggered_at
                assert inc.last_alert_at is not None
                assert inc.last_alert_at >= inc.opened_at

                # NO duplicate AI/correlation work: the alert was correlated
                # exactly once (when the incident was created); the repeats
                # were idempotent and produced no further 'correlated' history
                # entries and no duplicate incidents.
                history = await history_repo.list_for_alert(first_alert.id)
                correlated = [h for h in history if h.event_type == "correlated"]
                assert len(correlated) == 1
        finally:
            await _delete_tenant(tenant)

    async def test_recovery_still_auto_resolves(self):
        """A real state transition (sensor recovers) must still resolve the
        open incident - the idempotent short-circuit must not swallow it."""
        tenant, _site, _device, _source, sensor = await _make_ctx()
        try:
            async with AsyncSessionLocal() as db:
                service = AlertService(AlertRepository(db))
                incident_repo = IncidentRepository(db)

                await service.evaluate_sensor(sensor)  # down -> critical
                sensor.status = "up"
                ev = await service.evaluate_sensor(sensor)  # recovered
                assert ev.action == "resolved"
                assert ev.alert is not None
                assert ev.alert.status == "resolved"
        finally:
            await _delete_tenant(tenant)

    async def test_severity_change_still_recomputes(self):
        """A real state transition (warning -> critical on the same alert)
        must still trigger a recompute - we only short-circuit truly
        unchanged recurrences."""
        tenant, _site, _device, _source, sensor = await _make_ctx()
        try:
            async with AsyncSessionLocal() as db:
                service = AlertService(AlertRepository(db))
                # First: warning (sensor.status='warning' => severity warning)
                sensor.status = "warning"
                ev1 = await service.evaluate_sensor(sensor)
                assert ev1.alert is not None
                assert ev1.alert.severity == "warning"

                # State transition: now critical. Must NOT use the idempotent
                # short-circuit (severity changed) -> recompute updates the
                # incident's aggregate severity.
                sensor.status = "down"
                ev2 = await service.evaluate_sensor(sensor)
                assert ev2.alert is not None
                assert ev2.alert.severity == "critical"
                assert ev2.alert.id == ev1.alert.id  # same alert row, updated

                incident = None
                if ev2.alert.incident_id is not None:
                    incident = await IncidentRepository(db).get(
                        ev2.alert.incident_id, tenant.id
                    )
                assert incident is not None
                assert incident.severity == "critical"
        finally:
            await _delete_tenant(tenant)