"""Copilot observability context (observability plan P10 slice 1).

Topic and host matching (pure); the context assembled from real rows for a
named host and for topics; and the chat route: observability context only for
callers who may read monitoring data, with the language model stubbed so no
model is called.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.collectors.base import NormalizedBundle, NormalizedDevice, NormalizedInterface, NormalizedSensorReading
from app.core.auth import create_access_token
from app.main import app
from app.models.device import Device
from app.models.normalized_event import NormalizedEvent
from app.models.sensor import Sensor
from app.models.sensor_baseline import SensorBaseline
from app.services import copilot_router as copilot_module
from app.services.copilot_observability import host_tokens, observability_context, topics_in
from app.services.ollama_client import IntentResult

NOW = datetime.now(timezone.utc)


def test_topic_and_host_matching():
    assert topics_in("why is the network slow?") == ["network", "traces"]
    assert topics_in("any anomalies or unusual spikes?") == ["anomalies"]
    assert topics_in("check the syslog for errors") == ["logs"]
    # Whole words: "login", "catalog" and "report" mention none of these.
    assert topics_in("login failed on the catalog report") == []
    assert host_tokens("Is core-sw-1 (10.0.0.2) up?") >= {"core-sw-1", "10.0.0.2"}


def _saver(db):
    from app.repositories.device_repository import DeviceRepository
    from app.repositories.event_repository import EventRepository
    from app.repositories.interface_repository import InterfaceRepository
    from app.repositories.metric_repository import MetricRepository
    from app.repositories.sensor_repository import SensorRepository
    from app.services.monitoring.bundle_saver import BundleSaver

    return BundleSaver(device_repo=DeviceRepository(db), interface_repo=InterfaceRepository(db),
                       sensor_repo=SensorRepository(db), metric_repo=MetricRepository(db), event_repo=EventRepository(db))


async def _estate(db, monitoring_source_factory):
    source = await monitoring_source_factory(source_type="snmp")
    await _saver(db).save(source, NormalizedBundle(
        devices=[NormalizedDevice(external_id="sw", name="core-sw-1", device_type="switch", ip_address="10.0.0.2")],
        interfaces=[NormalizedInterface(device_external_id="sw", external_id="1", name="Gi0/1", admin_status="up", oper_status="down")],
        sensors=[NormalizedSensorReading(external_id="cpu", name="CPU load", metric_type="cpu_usage", value=99.0, unit="%",
                                         status="down", device_external_id="sw")],
    ))
    await db.commit()
    device = (await db.execute(select(Device).where(Device.tenant_id == source.tenant_id))).scalar_one()
    sensor = (await db.execute(select(Sensor).where(Sensor.monitoring_source_id == source.id))).scalar_one()
    db.add(NormalizedEvent(
        tenant_id=source.tenant_id, device_id=device.id, event_category="network", event_type="syslog.kern",
        severity="critical", title="LINK-3-UPDOWN Gi0/1 changed state to down", telemetry_type="LOG",
        attributes={"host": "core-sw-1"}, event_timestamp=NOW - timedelta(minutes=5), last_occurred_at=NOW, occurrence_count=1,
    ))
    db.add(SensorBaseline(tenant_id=source.tenant_id, sensor_id=sensor.id, enabled=True, direction="both", sensitivity=3.0,
                          stats={}, state="anomalous", last_value=99.0, last_expected=30.0, last_z=7.5, last_evaluated_at=NOW))
    await db.commit()
    return source.tenant_id


async def test_context_for_a_named_host_and_topics(db, monitoring_source_factory):
    tenant_id = await _estate(db, monitoring_source_factory)

    ctx, notes = await observability_context(db, tenant_id, "Why is core-sw-1 down? Check the network, logs and anything unusual")
    assert notes == []
    assert set(ctx) == {"observability_network", "observability_logs", "observability_anomalies", "observability_hosts"}
    [host] = ctx["observability_hosts"]
    assert (host["device"], host["address"]) == ("core-sw-1", "10.0.0.2")
    assert [s["sensor"] for s in host["failing_sensors"]] == ["CPU load"]
    assert [i["interface"] for i in host["interfaces_down_while_enabled"]] == ["Gi0/1"]
    assert host["recent_logs"][0]["message"].startswith("LINK-3-UPDOWN")
    assert ctx["observability_network"]["down_while_enabled"][0]["interface"] == "Gi0/1"
    assert ctx["observability_logs"]["warning_or_critical_lines"] == 1
    assert ctx["observability_anomalies"]["unusual_now"][0]["std_from_usual"] == 7.5

    # The address names the host too; a question about nothing here gets nothing.
    by_ip, _ = await observability_context(db, tenant_id, "what about 10.0.0.2?")
    assert [h["device"] for h in by_ip["observability_hosts"]] == ["core-sw-1"]
    assert await observability_context(db, tenant_id, "hello there") == ({}, [])
    # Another tenant sees none of it.
    other, _ = await observability_context(db, uuid.uuid4(), "core-sw-1 network logs")
    assert "observability_hosts" not in other and other["observability_logs"]["warning_or_critical_lines"] == 0
    await db.commit()


def _client(tenant_id, permissions):
    token = create_access_token(user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local",
                                is_superuser=False, permissions=permissions)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


async def test_chat_includes_observability_only_with_monitoring_read(db, monitoring_source_factory, monkeypatch):
    tenant_id = await _estate(db, monitoring_source_factory)
    seen: list[dict] = []

    async def classify(message):
        return IntentResult(intent="query", fallback_used=False)

    async def summarize(context):
        seen.append(context)
        return "summary"

    monkeypatch.setattr(copilot_module, "classify_intent", classify)
    monkeypatch.setattr(copilot_module, "summarize_context", summarize)
    question = {"message": "Is core-sw-1 healthy? Check its network interfaces."}

    async with _client(tenant_id, frozenset({"incidents.read"})) as client:
        r = await client.post("/api/copilot/chat", json=question)
        assert r.status_code == 200, r.text
        assert not any(k.startswith("observability_") for k in r.json()["context_used"]["data"])

    async with _client(tenant_id, frozenset({"incidents.read", "monitoring.read"})) as client:
        r = await client.post("/api/copilot/chat", json=question)
        data = r.json()["context_used"]["data"]
        assert {"observability_network", "observability_hosts"} <= set(data)
        assert data["observability_hosts"][0]["device"] == "core-sw-1"
    assert "observability_hosts" in seen[-1]  # the model is given the same real rows

    async with _client(tenant_id, frozenset({"monitoring.read"})) as client:
        assert (await client.post("/api/copilot/chat", json=question)).status_code == 403
    await db.commit()
