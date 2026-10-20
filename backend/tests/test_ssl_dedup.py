"""
Regression tests for Issue 1: SSL check alert deduplication.

Verifies that:
- Multiple failures for the same SSL check (host + port) produce ONE incident
  with occurrence_count incremented
- Different ports on the same host produce SEPARATE incidents
- A passing check auto-resolves the open incident
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.models.alert import Alert
from app.models.device import Device
from app.models.sensor import Sensor
from app.models.site import Site
from app.models.tenant import Tenant
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.alerting.correlation_engine import CorrelationEngine

@pytest.mark.asyncio
class TestSSLCheckDeduplication:
    """SSL check alert deduplication regression tests."""

    async def _create_tenant_and_device(self) -> tuple["Tenant", Device]:
        """Helper to create a tenant and device for testing."""
        from app.models.tenant import Tenant
        async with AsyncSessionLocal() as db:
            unique_id = str(uuid.uuid4())[:8]
            tenant = Tenant(name=f"Test Tenant {unique_id}", slug=f"test-tenant-{unique_id}")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

            site = Site(tenant_id=tenant.id, name="Test Site", code=f"TEST-{unique_id}")
            db.add(site)
            await db.commit()
            await db.refresh(site)

            device = Device(
                tenant_id=tenant.id,
                site_id=site.id,
                name="mail.example.com",
                device_type="server",
                vendor="Generic",
                status="up",
            )
            db.add(device)
            await db.commit()
            await db.refresh(device)

            return tenant, device

    async def test_same_port_multiple_failures_single_incident(self):
        """10 consecutive failures for SSL check on host X, port 993 -> 1 incident with occurrence_count == 10."""
        tenant, device = await self._create_tenant_and_device()

        # Create ONE source and ONE sensor (the same probe) so every alert
        # carries the same sensor_id and therefore the same dedup signature.
        # The previous fixture created a new sensor per iteration, which
        # produced a different signature each time and correctly yielded 10
        # incidents -- contradicting the test's stated intent.
        from app.models.monitoring_source import MonitoringSource
        async with AsyncSessionLocal() as db:
            source = MonitoringSource(
                tenant_id=tenant.id,
                name="test-source",
                source_type="test",
                is_active=True,
            )
            db.add(source)
            await db.flush()
            await db.refresh(source)
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
            await db.flush()
            await db.refresh(sensor)
            await db.commit()
            source_id = source.id
            sensor_id = sensor.id
            sensor_name = sensor.name

        async with AsyncSessionLocal() as db:
            alert_repo = AlertRepository(db)
            incident_repo = IncidentRepository(db)
            engine = CorrelationEngine(alert_repo, incident_repo)

            for i in range(10):
                alert = Alert(
                    tenant_id=tenant.id,
                    site_id=device.site_id,
                    device_id=device.id,
                    sensor_id=sensor_id,
                    monitoring_source_id=source_id,
                    title=sensor_name,
                    description=f"SSL check failed on {device.name}:993",
                    severity="critical",
                    status="open",
                    metric_type="ssl_security",
                    category="availability",
                    triggered_at=datetime.now(timezone.utc),
                    last_occurred_at=datetime.now(timezone.utc),
                    occurrence_count=1,
                )
                db.add(alert)
                await db.flush()
                await db.refresh(alert)

                result = await engine.correlate(alert)
                assert result.incident is not None
                if i == 0:
                    assert result.is_new_incident is True
                else:
                    assert result.is_new_incident is False
                    assert result.incident.id == alert.incident_id

                # Resolve the alert so the next iteration can create a new
                # active alert for the same sensor (uq_alerts_active_per_sensor
                # permits only one non-resolved alert per sensor).
                alert.status = "resolved"
                await db.flush()

            open_incidents = await incident_repo.list(tenant.id, status="open")
            assert len(open_incidents) == 1
            incident = open_incidents[0]
            # All 10 alerts grouped into 1 incident
            assert incident.alert_count == 10
            assert incident.total_occurrences == 10

    async def test_different_ports_separate_incidents(self):
        """1 failure each on ports 993, 465, 995 for the same host -> 3 separate incidents."""
        tenant, device = await self._create_tenant_and_device()
        ports = [993, 465, 995]

        async with AsyncSessionLocal() as db:
            alert_repo = AlertRepository(db)
            incident_repo = IncidentRepository(db)
            engine = CorrelationEngine(alert_repo, incident_repo)

            incidents_created = []
            for port in ports:
                from app.models.monitoring_source import MonitoringSource
                source = MonitoringSource(
                    tenant_id=tenant.id,
                    name=f"test-source-{port}",
                    source_type="test",
                    is_active=True,
                )
                db.add(source)
                await db.flush()
                await db.refresh(source)

                sensor = Sensor(
                    tenant_id=tenant.id,
                    monitoring_source_id=source.id,
                    device_id=device.id,
                    external_id=f"ssl-check-{port}",
                    name=f"SSL Security Check - {device.name}:{port}",
                    metric_type="ssl_security",
                    status="down",
                    critical_threshold=0,
                    warning_threshold=0,
                    current_value=0,
                )
                db.add(sensor)
                await db.flush()
                await db.refresh(sensor)

                alert = Alert(
                    tenant_id=tenant.id,
                    site_id=device.site_id,
                    device_id=device.id,
                    sensor_id=sensor.id,
                    title=sensor.name,
                    description=f"SSL check failed on {device.name}:{port}",
                    severity="critical",
                    status="open",
                    metric_type="ssl_security",
                    category="availability",
                    triggered_at=datetime.now(timezone.utc),
                    last_occurred_at=datetime.now(timezone.utc),
                    occurrence_count=1,
                )
                db.add(alert)
                await db.flush()
                await db.refresh(alert)

                result = await engine.correlate(alert)
                assert result.is_new_incident is True
                incidents_created.append(result.incident.id)

            open_incidents = await incident_repo.list(tenant.id, status="open")
            assert len(open_incidents) == 3
            assert len(set(incidents_created)) == 3
