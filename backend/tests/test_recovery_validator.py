"""Automated remediation is validated from fresh telemetry, never assumed."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.models.alert import Alert
from app.models.sensor import Sensor
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.events.autonomous_lifecycle import AutonomousLifecycleOrchestrator
from app.services.events.event_bus import DomainEvent, EventType
from app.services.events.recovery_validator import RecoveryValidator

NOW = datetime.now(timezone.utc)
REMEDIATED_AT = NOW - timedelta(minutes=5)


class FakeBus:
    def __init__(self):
        self.published: list[DomainEvent] = []
        self.handlers: dict = {}

    def subscribe(self, event_type=None):
        def decorator(handler):
            self.handlers.setdefault(event_type, []).append(handler)
            return handler
        return decorator

    async def publish(self, event):
        self.published.append(event)


class CountingSleep:
    def __init__(self):
        self.calls = 0

    async def __call__(self, seconds):
        self.calls += 1


async def _incident(db, tenant, source, *, sensor_status, seen_at, alert_status="resolved"):
    device, _ = await DeviceRepository(db).upsert(
        tenant_id=tenant.id, monitoring_source_id=source.id,
        external_id=f"dev-{uuid.uuid4().hex[:8]}", name="edge-fw-01",
        device_type="firewall", status="up",
    )
    device.last_seen_at = seen_at
    sensor = Sensor(
        tenant_id=tenant.id, monitoring_source_id=source.id, device_id=device.id,
        external_id=f"s-{uuid.uuid4().hex[:8]}", name="VPN tunnel", metric_type="vpn",
        status=sensor_status, last_value_at=seen_at,
    )
    db.add(sensor)
    problem_at = REMEDIATED_AT - timedelta(minutes=30)
    incident = await IncidentRepository(db).create(
        tenant_id=tenant.id, title="VPN tunnel down", status="remediating",
        severity="critical", opened_at=problem_at, last_alert_at=problem_at,
    )
    await db.flush()
    db.add(Alert(
        tenant_id=tenant.id, monitoring_source_id=source.id, device_id=device.id,
        sensor_id=sensor.id, incident_id=incident.id, severity="critical",
        status=alert_status, title="Tunnel down", triggered_at=problem_at,
        last_occurred_at=problem_at, resolved_at=NOW if alert_status == "resolved" else None,
    ))
    await db.commit()
    return incident


def _started(tenant, incident_id) -> DomainEvent:
    return DomainEvent(
        event_type=EventType.VALIDATION_STARTED, tenant_id=tenant.id, timestamp=REMEDIATED_AT,
        payload={"ticket_id": "t-1", "incident_id": str(incident_id) if incident_id else None, "sop_id": "s-1"},
    )


def _validator(bus, sleep, **kw) -> RecoveryValidator:
    return RecoveryValidator(bus, session_factory=AsyncSessionLocal, sleep=sleep, interval_seconds=0, **kw)


async def test_passes_after_consecutive_fresh_recovered_checks(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    incident = await _incident(db, tenant, source, sensor_status="up", seen_at=NOW)
    bus, sleep = FakeBus(), CountingSleep()

    outcome = await _validator(bus, sleep, consecutive_passes=2).validate(_started(tenant, incident.id))

    assert outcome == "passed"
    assert sleep.calls == 1  # two evaluations, one wait between them
    event = bus.published[-1]
    assert event.event_type == EventType.VALIDATION_PASSED
    assert event.payload["evidence"]["verdict"] == "recovered"


async def test_failing_telemetry_fails_immediately_with_evidence(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    incident = await _incident(db, tenant, source, sensor_status="down", seen_at=NOW)
    bus, sleep = FakeBus(), CountingSleep()

    outcome = await _validator(bus, sleep).validate(_started(tenant, incident.id))

    assert outcome == "failed" and sleep.calls == 0
    event = bus.published[-1]
    assert event.event_type == EventType.VALIDATION_FAILED
    assert "sensor is down" in event.payload["failure_reason"]
    assert event.payload["evidence"]["verdict"] == "not_recovered"


async def test_telemetry_from_before_the_remediation_never_passes(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    before = REMEDIATED_AT - timedelta(minutes=1)
    incident = await _incident(db, tenant, source, sensor_status="up", seen_at=before)
    bus, sleep = FakeBus(), CountingSleep()

    outcome = await _validator(bus, sleep, attempts=3).validate(_started(tenant, incident.id))

    assert outcome == "failed"
    assert sleep.calls == 2
    assert "not verified after 3 checks" in bus.published[-1].payload["failure_reason"]


async def test_missing_incident_fails(tenant):
    bus, sleep = FakeBus(), CountingSleep()
    assert await _validator(bus, sleep).validate(_started(tenant, None)) == "failed"
    assert bus.published[-1].event_type == EventType.VALIDATION_FAILED


async def test_failed_sop_is_escalated_not_validated(tenant):
    bus = FakeBus()
    orchestrator = AutonomousLifecycleOrchestrator(bus, session_factory=AsyncSessionLocal)
    assert EventType.VALIDATION_STARTED in bus.handlers  # validator registered

    await orchestrator._on_sop_completed(DomainEvent(
        event_type=EventType.SOP_COMPLETED, tenant_id=tenant.id,
        payload={"ticket_id": "t-1", "incident_id": str(uuid.uuid4()), "sop_id": "s-1",
                 "success": False, "result_notes": "step 2 timed out"},
    ))

    types = [e.event_type for e in bus.published]
    assert types == [EventType.VALIDATION_FAILED]
    assert bus.published[0].payload["failure_reason"] == "step 2 timed out"
