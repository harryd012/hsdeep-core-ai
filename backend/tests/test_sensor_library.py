"""Sensor library (observability plan P3 slice 1).

Catalogue rules and coverage rules (pure); tenant usage, bulk threshold
application through the sensor lifecycle with alerts, audit, RBAC and tenant
isolation; and per-host recommendations.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from app.collectors.base import NormalizedBundle, NormalizedDevice, NormalizedSensorReading
from app.core.auth import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.device import Device
from app.models.sensor import Sensor
from app.schemas.monitoring import SensorThresholdsIn
from app.services.monitoring.sensor_library import (
    TEMPLATES,
    TEMPLATES_BY_KEY,
    check_thresholds,
    default_thresholds,
    rules_for,
)

READER = frozenset({"monitoring.read"})
WRITER = frozenset({"monitoring.read", "monitoring.update"})


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(
        device_repo=DeviceRepository(db), interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db), metric_repo=MetricRepository(db), event_repo=EventRepository(db),
    )


def _client(tenant_id, permissions=READER):
    token = create_access_token(
        user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
        is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_catalogue_is_consistent():
    keys = [t.key for t in TEMPLATES]
    assert len(keys) == len(set(keys))
    metrics = [m for t in TEMPLATES for m in t.metric_types]
    assert len(metrics) == len(set(metrics)), "a metric type belongs to one template"
    assert all(t.direction in (None, "above", "below") and t.source_types for t in TEMPLATES)
    # Defaults come only from values the platform already defines.
    assert default_thresholds(TEMPLATES_BY_KEY["cpu_utilization"])[:2] == (80.0, 95.0)
    assert default_thresholds(TEMPLATES_BY_KEY["disk_usage"]) == (None, None, None)
    assert not TEMPLATES_BY_KEY["prtg_sensor"].thresholds_applicable


def test_threshold_order_and_coverage_rules():
    check_thresholds(SensorThresholdsIn(warning=80, critical=95, direction="above"))
    check_thresholds(SensorThresholdsIn(warning=20, critical=10, direction="below"))
    for bad in (SensorThresholdsIn(direction="above"), SensorThresholdsIn(warning=95, critical=80, direction="above"),
                SensorThresholdsIn(warning=10, critical=20, direction="below")):
        with pytest.raises(ValueError):
            check_thresholds(bad)

    assert rules_for("server", {"connectivity"}, 2, 0) == ["server_without_os"]
    assert rules_for("server", {"windows_winrm"}, 5, 0) == []
    assert rules_for("switch", set(), 0, 0) == ["unmonitored", "network_without_polling"]
    assert rules_for("switch", set(), 0, 24) == []  # SNMP interfaces count as polled
    assert rules_for("unknown", set(), 0, 0) == ["unmonitored", "unclassified"]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _bundle() -> NormalizedBundle:
    return NormalizedBundle(
        devices=[
            NormalizedDevice(external_id="srv-a", name="srv-a", device_type="server", ip_address="10.0.0.1"),
            NormalizedDevice(external_id="srv-b", name="srv-b", device_type="server", ip_address="10.0.0.2"),
            NormalizedDevice(external_id="sw-1", name="sw-1", device_type="switch", ip_address="10.0.0.3"),
            NormalizedDevice(external_id="mystery", name="mystery", device_type="unknown"),
        ],
        sensors=[
            NormalizedSensorReading(external_id="a-cpu", name="srv-a CPU", metric_type="cpu_usage", value=42.0, unit="%",
                                    status="up", device_external_id="srv-a"),
            NormalizedSensorReading(external_id="b-cpu", name="srv-b CPU", metric_type="cpu_usage", value=12.0, unit="%",
                                    status="up", device_external_id="srv-b"),
            NormalizedSensorReading(external_id="a-mem", name="srv-a memory", metric_type="memory_usage", value=50.0,
                                    unit="%", status="up", device_external_id="srv-a"),
            NormalizedSensorReading(external_id="a-odd", name="srv-a custom", metric_type="custom.queue.depth",
                                    value=3.0, status="up", device_external_id="srv-a"),
        ],
    )


@pytest.mark.usefixtures("seeded_subscription")
async def test_library_usage_apply_audit_rbac_and_isolation(db, monitoring_source_factory):
    from app.models.knowledge_audit import AuditEvent

    source = await monitoring_source_factory(source_type="windows_winrm")
    await _saver(db).save(source, _bundle())
    await db.commit()
    tenant_id = source.tenant_id
    srv_a = (await db.execute(select(Device).where(Device.tenant_id == tenant_id, Device.name == "srv-a"))).scalar_one()

    async with _client(tenant_id) as client:
        lib = (await client.get("/api/monitoring/sensor-library")).json()
        by_key = {t["key"]: t for t in lib["templates"]}
        cpu = by_key["cpu_utilization"]
        assert (cpu["sensors"], cpu["by_status"], cpu["reporting_source_types"]) == (2, {"up": 2}, ["windows_winrm"])
        assert (cpu["default_warning"], cpu["default_critical"], cpu["unit"]) == (80.0, 95.0, "%")
        assert by_key["latency"]["sensors"] == 0
        assert [u["metric_type"] for u in lib["uncatalogued"]] == ["custom.queue.depth"]
        # monitoring.read alone cannot apply.
        r = await client.post("/api/monitoring/sensor-library/templates/cpu_utilization/apply",
                              json={"thresholds": {"warning": 80, "critical": 95}})
        assert r.status_code == 403

    url = "/api/monitoring/sensor-library/templates/{}/apply"
    async with _client(tenant_id, WRITER) as client:
        assert (await client.post(url.format("nope"), json={"reset": True})).status_code == 404
        assert (await client.post(url.format("prtg_sensor"), json={"reset": True})).status_code == 422
        assert (await client.post(url.format("cpu_utilization"), json={})).status_code == 422
        bad = await client.post(url.format("cpu_utilization"), json={"thresholds": {"warning": 95, "critical": 80}})
        assert bad.status_code == 422

        dry = (await client.post(url.format("cpu_utilization"),
                                 json={"thresholds": {"warning": 30, "critical": 40}, "dry_run": True})).json()
        assert (dry["matched"], dry["changed"], dry["dry_run"]) == (2, 0, True)

        # Only srv-a's CPU: 42 % crosses the new critical of 40 at once.
        foreign = uuid.uuid4()
        done = (await client.post(url.format("cpu_utilization"),
                                  json={"thresholds": {"warning": 30, "critical": 40}, "device_ids": [str(srv_a.id), str(foreign)]})).json()
        assert (done["matched"], done["changed"], done["status_changed"], done["not_found"]) == (1, 1, 1, [str(foreign)])

    sensors = {s.external_id: s for s in (await db.execute(select(Sensor).where(Sensor.monitoring_source_id == source.id))).scalars()}
    for s in sensors.values():
        await db.refresh(s)
    a_cpu, b_cpu = sensors["a-cpu"], sensors["b-cpu"]
    assert (a_cpu.status, a_cpu.threshold_source, a_cpu.critical_threshold) == ("down", "user", 40.0)
    assert (b_cpu.status, b_cpu.threshold_source) == ("up", "provider")
    alert = (await db.execute(select(Alert).where(Alert.sensor_id == a_cpu.id))).scalar_one()
    assert alert.status != "resolved" and alert.severity == "critical"

    async with _client(tenant_id, WRITER) as client:
        # Reset returns to the provider's thresholds everywhere the template matches.
        done = (await client.post(url.format("cpu_utilization"), json={"reset": True})).json()
        assert (done["matched"], done["changed"]) == (2, 1)

    events = (
        await db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.action == "sensor.template_applied")
                         .order_by(AuditEvent.created_at))
    ).scalars().all()
    assert len(events) == 2 and events[0].new_values["critical_threshold"] == 40.0
    assert events[1].new_values["threshold_source"] == "provider"

    # Another tenant sees none of these sensors and cannot touch them.
    async with _client(uuid.uuid4(), WRITER) as client:
        lib = (await client.get("/api/monitoring/sensor-library")).json()
        assert all(t["sensors"] == 0 for t in lib["templates"]) and lib["uncatalogued"] == []
        r = await client.post(url.format("cpu_utilization"),
                              json={"thresholds": {"warning": 1, "critical": 2}, "device_ids": [str(srv_a.id)]})
        assert r.status_code == 402 or (r.json()["matched"], r.json()["not_found"]) == (0, [str(srv_a.id)])


async def test_recommendations_per_host(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="connectivity")
    await _saver(db).save(source, _bundle())
    await db.commit()

    async with _client(source.tenant_id) as client:
        body = (await client.get("/api/monitoring/sensor-library/recommendations")).json()
        hosts = {h["name"]: h["rules"] for h in body["hosts"]}
        # The servers report only through a connectivity source: no OS metrics.
        assert hosts == {
            "mystery": ["unmonitored", "unclassified"],
            "srv-a": ["server_without_os"],
            "srv-b": ["server_without_os"],
            "sw-1": ["unmonitored", "network_without_polling"],
        }
        counts = {r["key"]: r["hosts"] for r in body["rules"]}
        assert counts == {"unmonitored": 2, "server_without_os": 2, "network_without_polling": 1, "unclassified": 1}
        page = (await client.get("/api/monitoring/sensor-library/recommendations",
                                 params={"rule": "server_without_os", "limit": 1})).json()
        assert (page["total"], len(page["hosts"]), page["truncated"]) == (2, 1, True)
        assert (await client.get("/api/monitoring/sensor-library/recommendations", params={"rule": "x"})).status_code == 422

    async with _client(uuid.uuid4()) as client:
        assert (await client.get("/api/monitoring/sensor-library/recommendations")).json()["total"] == 0
