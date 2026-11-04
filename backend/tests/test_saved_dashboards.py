"""Saved dashboards and the widget registry (observability plan P6 slice 2).

Registry validation (pure); and the API: ownership, sharing permission,
widget targets limited to the caller's tenant, conflicts, audit and tenant
isolation.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.collectors.base import NormalizedBundle, NormalizedDevice, NormalizedInterface, NormalizedSensorReading
from app.core.auth import create_access_token
from app.main import app
from app.models.interface import Interface
from app.models.sensor import Sensor
from app.schemas.dashboards import WIDGET_TYPES, DashboardCreate, Widget

READER = frozenset({"dashboard.read"})
SHARER = frozenset({"dashboard.read", "devices.write"})


def _client(tenant_id, permissions=READER, user_id: uuid.UUID | None = None):
    token = create_access_token(
        user_id=user_id or uuid.UUID(int=1), tenant_id=tenant_id, email="noc@hsdeep.local",
        is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


def test_widget_registry_validation():
    w = Widget(id="links", type="busiest_links", config={})
    assert (w.size, w.config) == ("medium", {"limit": 5})
    sid = uuid.uuid4()
    chart = Widget(id="cpu", type="sensor_chart", size="large", config={"sensor_id": str(sid), "hours": 168})
    assert chart.config == {"sensor_id": str(sid), "channel": "primary", "hours": 168}
    bad = [
        {"id": "x", "type": "pie_of_everything"},
        {"id": "x", "type": "busiest_links", "config": {"limit": 50}},
        {"id": "x", "type": "busiest_links", "config": {"evil": True}},
        {"id": "x", "type": "sensor_chart", "config": {"hours": 24}},
        {"id": "x", "type": "interface_chart", "config": {"interface_id": str(sid), "channel": "cpu"}},
        {"id": "x", "type": "sensor_chart", "config": {"sensor_id": str(sid), "hours": 5}},
        {"id": "Bad Id!", "type": "vpn_tunnels"},
        {"id": "x", "type": "vpn_tunnels", "size": "huge"},
    ]
    for raw in bad:
        with pytest.raises(ValidationError):
            Widget.model_validate(raw)
    with pytest.raises(ValidationError):
        DashboardCreate(name="dup", widgets=[{"id": "a", "type": "vpn_tunnels"}, {"id": "a", "type": "network_summary"}])
    with pytest.raises(ValidationError):
        DashboardCreate(name="   ")
    assert len(WIDGET_TYPES) == 9


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


async def test_dashboards_api_ownership_sharing_targets_audit_isolation(db, monitoring_source_factory):
    from app.models.knowledge_audit import AuditEvent

    source = await monitoring_source_factory(source_type="snmp")
    await _saver(db).save(source, NormalizedBundle(
        devices=[NormalizedDevice(external_id="sw", name="sw", device_type="switch")],
        interfaces=[NormalizedInterface(device_external_id="sw", external_id="1", name="Gi0/1")],
        sensors=[NormalizedSensorReading(external_id="cpu", name="CPU", metric_type="cpu_usage", value=5.0, device_external_id="sw")],
    ))
    await db.commit()
    tenant_id = source.tenant_id
    sensor = (await db.execute(select(Sensor).where(Sensor.monitoring_source_id == source.id))).scalar_one()
    iface = (await db.execute(select(Interface).where(Interface.monitoring_source_id == source.id))).scalar_one()
    # Audit events reference users.id; the zero id is recorded with no actor
    # (as in the other API suites). Bob never writes successfully.
    alice, bob = uuid.UUID(int=0), uuid.uuid4()
    body = {
        "name": "NOC wall",
        "widgets": [
            {"id": "net", "type": "network_summary", "size": "large"},
            {"id": "cpu", "type": "sensor_chart", "config": {"sensor_id": str(sensor.id)}},
            {"id": "up", "type": "interface_chart", "config": {"interface_id": str(iface.id), "channel": "tx_bps"}},
        ],
    }

    async with _client(tenant_id, frozenset(), alice) as client:
        assert (await client.get("/api/dashboards")).status_code == 403

    async with _client(tenant_id, READER, alice) as client:
        reg = (await client.get("/api/dashboards/widgets")).json()
        assert {w["type"] for w in reg} == set(WIDGET_TYPES) and all("properties" in w["settings"] or w["settings"] for w in reg)
        # A reader may keep a dashboard, but not share it.
        assert (await client.post("/api/dashboards", json={**body, "shared": True})).status_code == 403
        created = await client.post("/api/dashboards", json=body)
        assert created.status_code == 201
        d = created.json()
        assert (d["owned"], d["shared"], [w["id"] for w in d["widgets"]]) == (True, False, ["net", "cpu", "up"])
        assert d["widgets"][1]["config"] == {"sensor_id": str(sensor.id), "channel": "primary", "hours": 24}
        assert (await client.post("/api/dashboards", json=body)).status_code == 409
        # A widget must not name another tenant's (or a missing) sensor.
        foreign = {"name": "x", "widgets": [{"id": "c", "type": "sensor_chart", "config": {"sensor_id": str(uuid.uuid4())}}]}
        assert (await client.post("/api/dashboards", json=foreign)).status_code == 422
        patched = (await client.patch(f"/api/dashboards/{d['id']}", json={"name": "NOC wall 2", "widgets": body["widgets"][:1]})).json()
        assert (patched["name"], len(patched["widgets"])) == ("NOC wall 2", 1)
        assert [x["name"] for x in (await client.get("/api/dashboards")).json()] == ["NOC wall 2"]

    # Bob sees nothing private, cannot touch Alice's; after she shares, he can read only.
    async with _client(tenant_id, READER, bob) as client:
        assert (await client.get("/api/dashboards")).json() == []
        assert (await client.get(f"/api/dashboards/{d['id']}")).status_code == 404
    async with _client(tenant_id, SHARER, alice) as client:
        assert (await client.patch(f"/api/dashboards/{d['id']}", json={"shared": True})).json()["shared"] is True
    async with _client(tenant_id, READER, bob) as client:
        listed = (await client.get("/api/dashboards")).json()
        assert [(x["name"], x["owned"]) for x in listed] == [("NOC wall 2", False)]
        assert (await client.get(f"/api/dashboards/{d['id']}")).json()["owned"] is False
        assert (await client.patch(f"/api/dashboards/{d['id']}", json={"name": "mine now"})).status_code == 404
        assert (await client.delete(f"/api/dashboards/{d['id']}")).status_code == 404

    async with _client(uuid.uuid4(), SHARER, alice) as client:
        assert (await client.get("/api/dashboards")).json() == []
        assert (await client.get(f"/api/dashboards/{d['id']}")).status_code == 404

    async with _client(tenant_id, READER, alice) as client:
        assert (await client.delete(f"/api/dashboards/{d['id']}")).status_code == 204
        assert (await client.get(f"/api/dashboards/{d['id']}")).status_code == 404

    actions = [
        e.action for e in (await db.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.entity_type == "saved_dashboard")
            .order_by(AuditEvent.created_at)
        )).scalars()
    ]
    assert actions == ["dashboard.created", "dashboard.updated", "dashboard.updated", "dashboard.deleted"]
