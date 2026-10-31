"""Incident recovery check: recovery needs fresh telemetry, never assumption."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.sensor import Sensor
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.recovery_check import RecoveryCheckItem, check_incident_recovery, verdict_of

pytestmark = pytest.mark.usefixtures("seeded_subscription")

NOW = datetime.now(timezone.utc)
PROBLEM_AT = NOW - timedelta(minutes=30)


def _item(result: str) -> RecoveryCheckItem:
    return RecoveryCheckItem("sensor", uuid.uuid4(), "s", "up", None, result, "")


def test_verdict_rules():
    assert verdict_of([]) == "inconclusive"
    assert verdict_of([_item("pass"), _item("pass")]) == "recovered"
    assert verdict_of([_item("pass"), _item("inconclusive")]) == "inconclusive"
    assert verdict_of([_item("inconclusive"), _item("fail")]) == "not_recovered"


async def _scenario(db, tenant, source, *, alert_status, sensor_status, sensor_seen, device_status, device_seen):
    device, _ = await DeviceRepository(db).upsert(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        external_id=f"dev-{uuid.uuid4().hex[:8]}",
        name="core-sw-01",
        device_type="switch",
        status=device_status,
    )
    device.last_seen_at = device_seen
    sensor = Sensor(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        device_id=device.id,
        external_id=f"sensor-{uuid.uuid4().hex[:8]}",
        name="Ping",
        metric_type="ping",
        status=sensor_status,
        last_value_at=sensor_seen,
    )
    db.add(sensor)
    incident = await IncidentRepository(db).create(
        tenant_id=tenant.id, title="core-sw-01 unreachable", status="investigating",
        severity="critical", opened_at=PROBLEM_AT, last_alert_at=PROBLEM_AT,
    )
    await db.flush()
    db.add(Alert(
        tenant_id=tenant.id,
        monitoring_source_id=source.id,
        device_id=device.id,
        sensor_id=sensor.id,
        incident_id=incident.id,
        severity="critical",
        status=alert_status,
        title="Ping down",
        triggered_at=PROBLEM_AT,
        last_occurred_at=PROBLEM_AT,
        resolved_at=NOW if alert_status == "resolved" else None,
    ))
    await db.commit()
    return incident


async def test_recovered_with_fresh_up_telemetry(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    incident = await _scenario(
        db, tenant, source, alert_status="resolved",
        sensor_status="up", sensor_seen=NOW, device_status="up", device_seen=NOW,
    )
    result = await check_incident_recovery(db, incident.id, tenant.id)
    assert result.verdict == "recovered"
    assert [(c.kind, c.result) for c in result.checks] == [
        ("alert", "pass"), ("sensor", "pass"), ("device", "pass"),
    ]


async def test_open_alert_and_down_sensor_is_not_recovered(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    incident = await _scenario(
        db, tenant, source, alert_status="open",
        sensor_status="down", sensor_seen=NOW, device_status="up", device_seen=NOW,
    )
    result = await check_incident_recovery(db, incident.id, tenant.id)
    assert result.verdict == "not_recovered"
    failing = {c.kind for c in result.checks if c.result == "fail"}
    assert failing == {"alert", "sensor"}


async def test_up_but_stale_telemetry_is_inconclusive(tenant, db, monitoring_source_factory):
    """An 'up' reading taken before the problem proves nothing about recovery."""
    source = await monitoring_source_factory()
    stale = PROBLEM_AT - timedelta(minutes=5)
    incident = await _scenario(
        db, tenant, source, alert_status="resolved",
        sensor_status="up", sensor_seen=stale, device_status="up", device_seen=None,
    )
    result = await check_incident_recovery(db, incident.id, tenant.id)
    assert result.verdict == "inconclusive"
    assert {c.kind: c.result for c in result.checks} == {
        "alert": "pass", "sensor": "inconclusive", "device": "inconclusive",
    }


async def test_incident_without_evidence_is_inconclusive(tenant, db):
    incident = await IncidentRepository(db).create(
        tenant_id=tenant.id, title="Manual incident", status="open",
        severity="warning", opened_at=PROBLEM_AT, last_alert_at=PROBLEM_AT,
    )
    await db.commit()
    result = await check_incident_recovery(db, incident.id, tenant.id)
    assert result.verdict == "inconclusive"
    assert result.checks == []


async def test_recovery_check_api_and_tenant_isolation(tenant, db, monitoring_source_factory):
    source = await monitoring_source_factory()
    incident = await _scenario(
        db, tenant, source, alert_status="resolved",
        sensor_status="up", sensor_seen=NOW, device_status="up", device_seen=NOW,
    )

    def client(tenant_id):
        token = create_access_token(
            user_id=uuid.uuid4(), tenant_id=tenant_id, email="rc@hsdeep.local",
            is_superuser=False, permissions=frozenset({"incidents.read"}),
        )
        return httpx.AsyncClient(app=app, base_url="http://test", headers={"Authorization": f"Bearer {token}"})

    async with client(tenant.id) as c:
        own = await c.get(f"/api/incidents/{incident.id}/recovery-check")
    assert own.status_code == 200, own.text
    body = own.json()
    assert body["verdict"] == "recovered"
    assert len(body["checks"]) == 3

    async with client(uuid.uuid4()) as c:
        foreign = await c.get(f"/api/incidents/{incident.id}/recovery-check")
    assert foreign.status_code == 404
