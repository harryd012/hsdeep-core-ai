"""Integration Center (observability plan P12 slice 1).

One derived state per source from recorded facts (failing / degraded / stale /
never synced / disabled / healthy), 24 h run statistics, sensor and device
counts, generic integrations alongside, sync history per source; synthetic
test sources left out; RBAC and tenant isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.collectors.base import NormalizedBundle, NormalizedDevice, NormalizedSensorReading
from app.core.auth import create_access_token
from app.main import app
from app.models.collector_run import CollectorRun
from app.models.integration import Integration
from app.models.source_health_audit import SourceHealthAudit
from app.services.monitoring.integration_center import source_state

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _client(tenant_id, permissions=frozenset({"monitoring.read"})):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(device_repo=DeviceRepository(db), interface_repo=InterfaceRepository(db),
                       sensor_repo=SensorRepository(db), metric_repo=MetricRepository(db), event_repo=EventRepository(db))


def _run(db, source, status, minutes_ago, error=None, seconds=2):
    start = NOW - timedelta(minutes=minutes_ago)
    db.add(CollectorRun(tenant_id=source.tenant_id, monitoring_source_id=source.id, trigger="scheduler", status=status,
                        error=error, sensors_upserted=1, started_at=start, finished_at=start + timedelta(seconds=seconds)))


async def test_integration_center_states_and_history(db, monitoring_source_factory):
    recent = NOW - timedelta(minutes=2)
    healthy = await monitoring_source_factory(name="a-healthy", source_type="snmp", status="connected",
                                              last_successful_run_at=recent, api_key_encrypted="ciphertext")
    failing = await monitoring_source_factory(name="b-failing", status="error", consecutive_failures=3,
                                              last_successful_run_at=NOW - timedelta(hours=5), last_error="x" * 800)
    degraded = await monitoring_source_factory(name="c-degraded", status="connected", health_status="degraded",
                                               last_successful_run_at=recent)
    stale = await monitoring_source_factory(name="d-stale", status="connected", poll_interval_seconds=60,
                                            last_successful_run_at=NOW - timedelta(minutes=20))
    never = await monitoring_source_factory(name="e-never")
    off = await monitoring_source_factory(name="f-off", is_active=False, last_successful_run_at=recent)
    await monitoring_source_factory(name="z-synthetic", source_type="test_fake_concurrency")
    tenant_id = healthy.tenant_id

    await _saver(db).save(healthy, NormalizedBundle(
        devices=[NormalizedDevice(external_id="r1", name="edge-r1", device_type="router")],
        sensors=[NormalizedSensorReading(external_id="cpu", name="CPU", metric_type="cpu_usage", value=5.0, unit="%",
                                         status="up", device_external_id="r1"),
                 NormalizedSensorReading(external_id="mem", name="Memory", metric_type="memory_usage", value=50.0, unit="%",
                                         status="up", device_external_id="r1")],
    ))
    # healthy: 3 runs in 24 h (one failed), one older run outside the window.
    _run(db, healthy, "success", 2, seconds=2)
    _run(db, healthy, "error", 30, error="timeout", seconds=4)
    _run(db, healthy, "partial", 60, seconds=3)
    _run(db, healthy, "success", 60 * 30)
    _run(db, failing, "error", 1, error="401 Unauthorized")
    db.add(SourceHealthAudit(tenant_id=tenant_id, monitoring_source_id=healthy.id, event_type="health_transition",
                             from_health_status="healthy", to_health_status="degraded", to_consecutive_failures=1,
                             reason="collection failed", created_at=NOW - timedelta(minutes=30)))
    db.add(Integration(tenant_id=tenant_id, name="ServiceNow", provider_type="itsm", provider="servicenow",
                       status="authentication_failed", credential_reference="vault://sn", last_error="bad token",
                       configuration={"endpoint": "https://sn.example"}))
    db.add(Integration(tenant_id=tenant_id, name="Syslog", provider_type="datacenter", provider="syslog", status="connected",
                       last_successful_collection=recent))
    await db.commit()

    async with _client(tenant_id) as client:
        r = await client.get("/api/integration-center")
        assert r.status_code == 200
        body = r.json()
        names = [(i["name"], i["state"]) for i in body["items"]]
        assert names == [("b-failing", "failing"), ("ServiceNow", "failing"), ("c-degraded", "degraded"),
                         ("d-stale", "stale"), ("e-never", "never_synced"), ("f-off", "disabled"),
                         ("a-healthy", "healthy"), ("Syslog", "healthy")]
        s = body["summary"]
        assert (s["total"], s["sources"], s["integrations"]) == (8, 6, 2)
        assert (s["failing"], s["degraded"], s["stale"], s["never_synced"], s["disabled"], s["healthy"]) == (2, 1, 1, 1, 1, 2)
        assert (s["runs_24h"], s["failed_runs_24h"]) == (4, 2)

        row = {i["name"]: i for i in body["items"]}
        h = row["a-healthy"]
        assert (h["runs_24h"], h["failed_runs_24h"], h["success_rate_24h"], h["avg_run_ms"]) == (3, 1, 66.7, 3000)
        assert (h["sensors"], h["devices"], h["credential_configured"], h["last_run_status"]) == (2, 1, True, "success")
        assert "api_key_encrypted" not in h and "ciphertext" not in r.text
        assert len(row["b-failing"]["last_error"]) == 500 and row["b-failing"]["consecutive_failures"] == 3
        sn = row["ServiceNow"]
        assert (sn["kind"], sn["type"], sn["endpoint"], sn["credential_configured"]) == ("integration", "servicenow", "https://sn.example", True)
        assert "vault://sn" not in r.text

        d = (await client.get(f"/api/integration-center/sources/{healthy.id}")).json()
        assert d["source"]["name"] == "a-healthy"
        assert [x["status"] for x in d["runs"]] == ["success", "error", "partial", "success"]
        assert (d["runs"][1]["error"], d["runs"][1]["duration_ms"]) == ("timeout", 4000)
        [ev] = d["health_events"]
        assert (ev["event_type"], ev["to_health_status"], ev["reason"]) == ("health_transition", "degraded", "collection failed")
        assert (await client.get(f"/api/integration-center/sources/{uuid.uuid4()}")).status_code == 404

    async with _client(tenant_id, permissions=frozenset()) as client:
        assert (await client.get("/api/integration-center")).status_code == 403
    async with _client(uuid.uuid4()) as client:
        other = (await client.get("/api/integration-center")).json()
        assert other["items"] == [] and other["summary"]["total"] == 0
        assert (await client.get(f"/api/integration-center/sources/{healthy.id}")).status_code == 404
    for src in (failing, degraded, stale, never, off):
        assert src.tenant_id == tenant_id


async def test_source_detail_hides_run_level_health_events(db, monitoring_source_factory):
    """State changes first: `run_recorded` rows and legacy `health_transition`
    rows with an unchanged health state are hidden unless asked for."""
    src = await monitoring_source_factory(name="prtg", status="error", health_status="degraded")

    def ev(event_type, frm, to, minutes):
        db.add(SourceHealthAudit(tenant_id=src.tenant_id, monitoring_source_id=src.id, event_type=event_type,
                                 from_health_status=frm, to_health_status=to, reason=event_type,
                                 created_at=NOW - timedelta(minutes=minutes)))

    ev("health_transition", "healthy", "degraded", 60)   # real change
    ev("health_transition", "degraded", "degraded", 50)  # legacy noise
    ev("health_transition", "degraded", "degraded", 40)  # legacy noise
    ev("run_recorded", "degraded", "degraded", 30)
    ev("timeout", None, None, 20)                        # notable, kept
    await db.commit()

    async with _client(src.tenant_id) as client:
        d = (await client.get(f"/api/integration-center/sources/{src.id}")).json()
        assert [(e["event_type"], e["from_health_status"]) for e in d["health_events"]] == [
            ("timeout", None), ("health_transition", "healthy")]
        assert (d["include_run_events"], d["hidden_run_events"]) == (False, 3)

        d = (await client.get(f"/api/integration-center/sources/{src.id}", params={"include_run_events": "true"})).json()
        assert len(d["health_events"]) == 5
        assert (d["include_run_events"], d["hidden_run_events"]) == (True, 0)


def test_stale_rule_has_a_floor():
    from app.models.monitoring_source import MonitoringSource

    src = MonitoringSource(is_active=True, status="connected", health_status="healthy", poll_interval_seconds=10,
                           last_successful_run_at=NOW - timedelta(minutes=10))
    assert source_state(src, NOW) == "healthy"  # 3 polls = 30 s, but the floor is 15 min
    src.last_successful_run_at = NOW - timedelta(minutes=16)
    assert source_state(src, NOW) == "stale"
    src.poll_interval_seconds = 600
    assert source_state(src, NOW) == "healthy"  # 3 polls = 30 min
