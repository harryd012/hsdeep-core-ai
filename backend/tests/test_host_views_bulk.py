"""Host saved views, bulk host actions and the explorer tag facet (observability plan P2 slice 2).

Saved views: owner / shared visibility, owner-only writes, share permission,
filter validation, name uniqueness, audit and tenant isolation. Bulk actions:
classification with per-host audit, tag add / remove, vocabulary checks, RBAC,
subscription gate, cross-tenant ids reported as not found, and pause / resume
through the sensor lifecycle (alerts resolved, disabled sensors untouched).
"""
from __future__ import annotations

import uuid

import httpx
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.alert import Alert
from app.models.device import Device
from app.models.knowledge_audit import AuditEvent
from app.models.sensor import Sensor
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.alert_repository import AlertRepository
from app.services.alert_service import AlertService

READER = frozenset({"devices.read"})
WRITER = frozenset({"devices.read", "devices.write"})
NOC = frozenset({"devices.read", "monitoring.update"})
VIEWS = "/api/explorer/hosts/views"
BULK = "/api/explorer/hosts/bulk"
BULK_MON = "/api/explorer/hosts/bulk/monitoring"


@pytest_asyncio.fixture
async def users(tenant):
    """Two real users of the tenant (audit events reference users.id)."""
    async with AsyncSessionLocal() as db:
        rows = [User(tenant_id=tenant.id, email=f"{n}-{uuid.uuid4().hex[:6]}@hsdeep.local") for n in ("alice", "bob")]
        db.add_all(rows)
        await db.commit()
        return rows[0].id, rows[1].id


def _client(tenant_id, permissions=WRITER, user_id=None):
    token = create_access_token(
        user_id=user_id or uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local", is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


def _s(ids) -> list[str]:
    return [str(i) for i in ids]


async def _devices(tenant_id, n=3, **fields) -> list[uuid.UUID]:
    async with AsyncSessionLocal() as db:
        rows = [Device(tenant_id=tenant_id, name=f"host-{i}-{uuid.uuid4().hex[:4]}", device_type="server", **fields) for i in range(n)]
        db.add_all(rows)
        await db.commit()
        return [d.id for d in rows]


async def _device(device_id) -> Device:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(Device).where(Device.id == device_id))).scalar_one()


async def _audit(tenant_id, prefix) -> list[AuditEvent]:
    async with AsyncSessionLocal() as db:
        return list((await db.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.action.like(f"{prefix}%"))
        )).scalars())


# ---------------------------------------------------------------------------
# Saved views
# ---------------------------------------------------------------------------


async def test_views_visibility_owner_writes_and_audit(tenant, users):
    alice_id, bob_id = users
    async with _client(tenant.id, WRITER, alice_id) as alice, _client(tenant.id, READER, bob_id) as bob:
        r = await alice.post(VIEWS, json={"name": " Prod Cisco ", "filters": {"vendor": "cisco", "environment": "production"}, "layout": "inventory"})
        assert r.status_code == 201, r.text
        private = r.json()
        assert private["name"] == "Prod Cisco" and private["owned"] is True and private["shared"] is False
        assert private["filters"]["vendor"] == "cisco" and private["filters"].get("search") is None

        shared = (await alice.post(VIEWS, json={"name": "Critical", "filters": {"health": "critical"}, "shared": True})).json()
        assert (await alice.post(VIEWS, json={"name": "Prod Cisco"})).status_code == 409

        # Bob sees only the shared view, cannot change or delete it, and cannot share his own without devices.write.
        listed = (await bob.get(VIEWS)).json()
        assert [v["id"] for v in listed] == [shared["id"]] and listed[0]["owned"] is False
        assert (await bob.patch(f"{VIEWS}/{shared['id']}", json={"name": "Mine"})).status_code == 404
        assert (await bob.delete(f"{VIEWS}/{shared['id']}")).status_code == 404
        assert (await bob.post(VIEWS, json={"name": "Bob shared", "shared": True})).status_code == 403
        bobs = (await bob.post(VIEWS, json={"name": "Prod Cisco"})).json()  # same name, other owner: allowed
        assert bobs["owned"] is True

        # Unknown filter keys and bad health values are rejected.
        assert (await alice.post(VIEWS, json={"name": "x", "filters": {"sql": "1=1"}})).status_code == 422
        assert (await alice.post(VIEWS, json={"name": "x", "filters": {"health": "healthy"}})).status_code == 422

        upd = await alice.patch(f"{VIEWS}/{private['id']}", json={"filters": {"vendor": "juniper"}, "shared": True})
        assert upd.status_code == 200 and upd.json()["filters"]["vendor"] == "juniper" and upd.json()["shared"] is True
        assert upd.json()["filters"].get("environment") is None  # filters are replaced, not merged
        assert (await alice.delete(f"{VIEWS}/{shared['id']}")).status_code == 204

        actions = sorted(e.action for e in await _audit(tenant.id, "host_view."))
        assert actions == ["host_view.created"] * 3 + ["host_view.deleted", "host_view.updated"]

    # Another tenant sees nothing and cannot reach the views.
    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        async with _client(other.id, WRITER, alice_id) as outsider:
            assert (await outsider.get(VIEWS)).json() == []
            assert (await outsider.patch(f"{VIEWS}/{private['id']}", json={"name": "stolen"})).status_code == 404
            assert (await outsider.delete(f"{VIEWS}/{private['id']}")).status_code == 404
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()


async def test_views_need_devices_read(tenant):
    async with _client(tenant.id, frozenset({"alerts.read"})) as c:
        assert (await c.get(VIEWS)).status_code == 403
        assert (await c.post(VIEWS, json={"name": "x"})).status_code == 403


# ---------------------------------------------------------------------------
# Bulk classification
# ---------------------------------------------------------------------------


async def test_bulk_classify_audit_vocabulary_and_isolation(tenant, seeded_subscription, users):
    ids = await _devices(tenant.id, 3)
    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        foreign = (await _devices(other.id, 1))[0]
        async with _client(tenant.id, WRITER, users[0]) as c:
            r = await c.post(BULK, json={"device_ids": _s([*ids, ids[0], foreign]), "action": "set_criticality", "value": "high"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert (body["requested"], body["matched"], body["changed"]) == (4, 3, 3)
            assert body["not_found"] == [str(foreign)]
            assert (await _device(foreign)).criticality is None
            assert {(await _device(i)).criticality for i in ids} == {"high"}

            # Same value again: nothing changes, nothing more is audited.
            again = (await c.post(BULK, json={"device_ids": _s(ids), "action": "set_criticality", "value": "high"})).json()
            assert again["changed"] == 0
            events = await _audit(tenant.id, "device.bulk.set_criticality")
            assert len(events) == 3 and events[0].old_values == {"criticality": None} and events[0].new_values == {"criticality": "high"}

            assert (await c.post(BULK, json={"device_ids": _s(ids), "action": "set_environment", "value": "prod"})).status_code == 422
            assert (await c.post(BULK, json={"device_ids": _s(ids), "action": "set_criticality", "value": "urgent"})).status_code == 422
            assert (await c.post(BULK, json={"device_ids": _s(ids), "action": "add_tags"})).status_code == 422
            assert (await c.post(BULK, json={"device_ids": _s(ids), "action": "add_tags", "tags": ["bad<tag>"]})).status_code == 422
            assert (await c.post(BULK, json={"device_ids": [], "action": "set_criticality"})).status_code == 422
            too_many = [str(uuid.uuid4()) for _ in range(201)]
            assert (await c.post(BULK, json={"device_ids": too_many, "action": "set_criticality"})).status_code == 422

            env = (await c.post(BULK, json={"device_ids": _s(ids[:2]), "action": "set_environment", "value": "production"})).json()
            assert env["changed"] == 2
            cleared = (await c.post(BULK, json={"device_ids": _s(ids[:1]), "action": "set_environment", "value": None})).json()
            assert cleared["changed"] == 1 and (await _device(ids[0])).environment is None

            add = (await c.post(BULK, json={"device_ids": _s(ids), "action": "add_tags", "tags": [" core ", "pci"]})).json()
            assert add["changed"] == 3 and (await _device(ids[1])).tags == ["core", "pci"]
            assert (await c.post(BULK, json={"device_ids": _s(ids), "action": "add_tags", "tags": ["core"]})).json()["changed"] == 0
            rm = (await c.post(BULK, json={"device_ids": _s(ids[:1]), "action": "remove_tags", "tags": ["pci", "absent"]})).json()
            assert rm["changed"] == 1 and (await _device(ids[0])).tags == ["core"]

            # The explorer filters and counts by tag.
            explorer = (await c.get("/api/explorer/hosts", params={"tag": "pci"})).json()
            assert {row["id"] for row in explorer["rows"]} == {str(i) for i in ids[1:]}
            assert all("pci" in row["tags"] for row in explorer["rows"])
            assert {f["value"]: f["count"] for f in explorer["facets"]["tags"]} == {"core": 3, "pci": 2}

            # Each route takes only its own actions.
            assert (await c.post(BULK, json={"device_ids": _s(ids), "action": "pause_monitoring"})).status_code == 422
            vocab = (await c.get("/api/explorer/hosts/vocabulary")).json()
            assert vocab["criticality"] == ["critical", "high", "medium", "low"] and "production" in vocab["environment"]

        async with _client(tenant.id, READER) as reader:
            assert (await reader.post(BULK, json={"device_ids": _s(ids), "action": "set_criticality", "value": "low"})).status_code == 403
        async with _client(tenant.id, NOC) as noc:  # monitoring.update alone cannot classify
            assert (await noc.post(BULK, json={"device_ids": _s(ids), "action": "set_criticality", "value": "low"})).status_code == 403
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()


async def test_bulk_requires_active_subscription(tenant):
    ids = await _devices(tenant.id, 1)
    async with _client(tenant.id, WRITER) as c:
        r = await c.post(BULK, json={"device_ids": _s(ids), "action": "set_criticality", "value": "low"})
        assert r.status_code in (402, 403), r.text
    assert (await _device(ids[0])).criticality is None


# ---------------------------------------------------------------------------
# Bulk pause / resume monitoring
# ---------------------------------------------------------------------------


async def test_bulk_pause_resume_monitoring(tenant, seeded_subscription, monitoring_source_factory, users):
    source = await monitoring_source_factory(source_type="prtg")
    ids = await _devices(tenant.id, 2)
    async with AsyncSessionLocal() as db:
        sensors = [
            Sensor(tenant_id=tenant.id, monitoring_source_id=source.id, device_id=ids[0], external_id="a", name="CPU",
                   metric_type="cpu", status="down", current_value=99.0),
            Sensor(tenant_id=tenant.id, monitoring_source_id=source.id, device_id=ids[0], external_id="b", name="Disk",
                   metric_type="disk", status="up"),
            Sensor(tenant_id=tenant.id, monitoring_source_id=source.id, device_id=ids[1], external_id="c", name="Ping",
                   metric_type="ping", status="disabled", admin_state="disabled"),
        ]
        db.add_all(sensors)
        await db.flush()
        await AlertService(AlertRepository(db)).evaluate_sensor(sensors[0])
        await db.commit()
        sensor_ids = [s.id for s in sensors]

    async def states():
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Sensor).where(Sensor.id.in_(sensor_ids)))).scalars()
            return {s.id: (s.admin_state, s.status) for s in rows}

    async def open_alerts():
        async with AsyncSessionLocal() as db:
            return (await db.execute(
                select(Alert).where(Alert.sensor_id == sensor_ids[0], Alert.status.in_(("open", "acknowledged")))
            )).scalars().all()

    assert len(await open_alerts()) == 1

    async with _client(tenant.id, WRITER) as writer:  # devices.write alone cannot pause monitoring
        assert (await writer.post(BULK_MON, json={"device_ids": _s(ids), "action": "pause_monitoring"})).status_code == 403

    async with _client(tenant.id, NOC, users[1]) as c:
        r = await c.post(BULK_MON, json={"device_ids": _s(ids), "action": "pause_monitoring"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert (body["sensors_changed"], body["changed"]) == (2, 1)  # the disabled sensor's host is untouched
        s = await states()
        assert s[sensor_ids[0]] == ("paused", "paused") and s[sensor_ids[1]] == ("paused", "paused")
        assert s[sensor_ids[2]] == ("disabled", "disabled")
        assert await open_alerts() == []

        resumed = (await c.post(BULK_MON, json={"device_ids": _s(ids), "action": "resume_monitoring"})).json()
        assert resumed["sensors_changed"] == 2
        s = await states()
        assert s[sensor_ids[0]] == ("active", "unknown") and s[sensor_ids[2]] == ("disabled", "disabled")

    events = await _audit(tenant.id, "device.bulk.")
    assert sorted(e.action for e in events) == ["device.bulk.pause_monitoring", "device.bulk.resume_monitoring"]
    assert events[0].entity_id == ids[0]
