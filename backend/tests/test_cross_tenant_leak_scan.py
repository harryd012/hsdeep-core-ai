"""
Cross-tenant leak scan (R1.3).

Seeds one row of every main tenant-scoped resource in tenant A, each named
with a unique marker, then calls EVERY GET route of the app as a user of
tenant B holding every permission (not a superuser). Path parameters are
filled with tenant A's real ids where the parameter names a seeded resource,
so detail routes are asked directly for A's rows. No response may contain any
of A's ids or markers.

A positive control runs the same scan as tenant A's own user and requires the
markers to appear, which proves the scan would see a real leak. Because the
route list comes from the app itself, new GET routes are covered without
editing this file.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.auth import create_access_token, hash_api_key
from app.core.db import AsyncSessionLocal
from app.core.permissions import ALL_PERMISSION_CODES
from app.main import app
from app.models.alert import Alert
from app.models.api_key import ApiKey
from app.models.device import Device
from app.models.incident import Incident
from app.models.integration import Integration
from app.models.knowledge_audit import AuditEvent
from app.models.monitoring_source import MonitoringSource
from app.models.plan import Plan
from app.models.runbook import Runbook
from app.models.sensor import Sensor
from app.models.site import Site
from app.models.sop import SOP, SOPVersion
from app.models.subscription import Subscription
from app.models.task import Task
from app.models.tenant import Tenant
from app.models.ticket import Ticket
from app.models.user import User

pytestmark = pytest.mark.asyncio
NOW = datetime.now(timezone.utc)

#: Path parameter name -> key of the seeded resource whose id fills it.
PARAM_RESOURCE = {
    "device_id": "device", "resource_id": "device", "site_id": "site", "sensor_id": "sensor",
    "alert_id": "alert", "incident_id": "incident", "ticket_id": "ticket", "sop_id": "sop",
    "runbook_id": "runbook", "integration_id": "integration", "task_id": "task", "source_id": "source",
    "tenant_id": "tenant", "user_id": "user",
}

#: Routes whose response is the same for everyone by design (no tenant data).
GLOBAL_ROUTES = {"/api/monitoring/source-types", "/api/auth/permissions"}


async def _tenant_with_plan(db, name: str) -> tuple[Tenant, Plan]:
    tenant = Tenant(name=name, slug=f"leak-{uuid.uuid4().hex[:8]}")
    plan = Plan(name=f"leak-{uuid.uuid4().hex[:8]}", tier=f"leak-{uuid.uuid4().hex[:8]}", price_monthly=0,
                price_annual=0, device_limit=100, sensor_limit=1000, api_rate_limit=1_000_000,
                collector_limit=10, seats=5, features_json={}, is_public=False)
    db.add_all([tenant, plan])
    await db.flush()
    db.add(Subscription(tenant_id=tenant.id, plan_id=plan.id, status="active", current_period_start=NOW,
                        current_period_end=NOW + timedelta(days=30), billing_interval="month"))
    return tenant, plan


@pytest_asyncio.fixture
async def estate():
    """Tenant A fully seeded, tenant B with one user; removed afterwards."""
    marker = f"LEAKMARK{uuid.uuid4().hex[:12]}"
    async with AsyncSessionLocal() as db:
        a, plan_a = await _tenant_with_plan(db, f"{marker} Tenant A")
        b, plan_b = await _tenant_with_plan(db, "Tenant B")
        site = Site(tenant_id=a.id, name=f"{marker} site", code=marker[-10:])
        source = MonitoringSource(tenant_id=a.id, name=f"{marker} source", source_type="prtg", is_active=True,
                                  poll_interval_seconds=300, status="active")
        db.add_all([site, source])
        await db.flush()
        device = Device(tenant_id=a.id, site_id=site.id, monitoring_source_id=source.id, name=f"{marker} device",
                        device_type="router", status="down", hostname=f"{marker}.example.test")
        db.add(device)
        await db.flush()
        sensor = Sensor(tenant_id=a.id, monitoring_source_id=source.id, device_id=device.id, external_id=marker,
                        name=f"{marker} sensor", metric_type="ping", status="down")
        incident = Incident(tenant_id=a.id, site_id=site.id, device_id=device.id, title=f"{marker} incident",
                            severity="critical", status="open", opened_at=NOW, last_alert_at=NOW)
        db.add_all([sensor, incident])
        await db.flush()
        alert = Alert(tenant_id=a.id, site_id=site.id, device_id=device.id, sensor_id=sensor.id,
                      incident_id=incident.id, monitoring_source_id=source.id, title=f"{marker} alert",
                      severity="critical", status="open", triggered_at=NOW, last_occurred_at=NOW)
        ticket = Ticket(tenant_id=a.id, ticket_number=f"T-{marker[-8:]}", title=f"{marker} ticket", opened_at=NOW)
        sop = SOP(tenant_id=a.id, sop_number=f"SOP-{marker[-6:]}", name=f"{marker} sop", category="availability")
        integration = Integration(tenant_id=a.id, name=f"{marker} integration", provider_type="itsm",
                                  provider="servicenow")
        user = User(tenant_id=a.id, email=f"{marker.lower()}@example.test", full_name=f"{marker} user")
        db.add_all([alert, ticket, sop, integration, user])
        await db.flush()
        version = SOPVersion(tenant_id=a.id, sop_id=sop.id, version_number=1, steps=[{"order": 1, "action": marker}],
                             possible_causes=[], status="approved", created_at=NOW)
        db.add(version)
        await db.flush()
        runbook = Runbook(tenant_id=a.id, sop_id=sop.id, sop_version_id=version.id, name=f"{marker} runbook")
        task = Task(tenant_id=a.id, task_number=f"TSK-{marker[-6:]}", title=f"{marker} task", task_type="investigate")
        key = ApiKey(tenant_id=a.id, name=f"{marker} key", key_prefix=marker[:8],
                     key_hash=hash_api_key(f"hsk_{marker}"), scopes=["devices.read"])
        audit = AuditEvent(tenant_id=a.id, action="leak.seeded", description=f"{marker} audit")
        db.add_all([runbook, task, key, audit])
        user_b = User(tenant_id=b.id, email=f"b-{uuid.uuid4().hex[:8]}@example.test")
        raw_key_b = f"hsk_{uuid.uuid4().hex}{uuid.uuid4().hex}"
        key_b = ApiKey(tenant_id=b.id, name="tenant B key", key_prefix=raw_key_b[:8],
                       key_hash=hash_api_key(raw_key_b), scopes=sorted(ALL_PERMISSION_CODES))
        db.add_all([user_b, key_b])
        await db.commit()

        ids = {
            "tenant": a.id, "site": site.id, "source": source.id, "device": device.id, "sensor": sensor.id,
            "incident": incident.id, "alert": alert.id, "ticket": ticket.id, "sop": sop.id,
            "sop_version": version.id, "runbook": runbook.id, "integration": integration.id, "task": task.id,
            "user": user.id, "api_key": key.id, "audit": audit.id,
        }
    yield {"marker": marker, "ids": ids, "a": a, "b": b, "user_a": user, "user_b": user_b, "key_b": raw_key_b}

    async with AsyncSessionLocal() as db:
        for t in (a, b):
            await db.execute(delete(Subscription).where(Subscription.tenant_id == t.id))
            await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.execute(delete(Plan).where(Plan.id.in_([plan_a.id, plan_b.id])))
        await db.commit()


def _all_permission_token(user: User) -> str:
    return create_access_token(user_id=user.id, tenant_id=user.tenant_id, email=user.email,
                               is_superuser=False, permissions=frozenset(ALL_PERMISSION_CODES))


def _get_routes():
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "GET" in methods and route.path.startswith("/api") and route.path not in GLOBAL_ROUTES:
            yield route.path


def _fill(path: str, ids: dict) -> str:
    def sub(m):
        key = PARAM_RESOURCE.get(m.group(1))
        return str(ids[key]) if key else str(uuid.uuid4())
    return re.sub(r"{(\w+)(?::\w+)?}", sub, path)


async def _scan(token: str, estate) -> tuple[dict[str, list[str]], dict[int, int]]:
    """GET every route; return {path: [leaked needles]} and a status histogram."""
    id_needles = [str(v) for v in estate["ids"].values()]
    leaks: dict[str, list[str]] = {}
    details: dict[str, str] = {}
    statuses: dict[int, int] = {}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        for path in _get_routes():
            url = _fill(path, estate["ids"])
            try:
                resp = await client.get(url)
            except Exception as exc:  # noqa: BLE001 - a crashing route cannot leak, but is reported
                statuses[-1] = statuses.get(-1, 0) + 1
                leaks.setdefault(f"{path} (raised {type(exc).__name__})", [])
                continue
            statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
            body = resp.text.lower()
            # Markers are names of A's rows: their presence is always data.
            # An id is the caller's own input when it is in the URL (routes echo
            # it: "device <id> not found", {"device_id": <id>, "upstream": []}),
            # and an error body cannot carry rows, so ids count only on success
            # and only when the caller did not send them.
            found = [estate["marker"]] if estate["marker"].lower() in body else []
            if resp.status_code < 400:
                found += [i for i in id_needles if i in body and i not in url]
            if found:
                leaks[path] = found
                details[path] = f"{resp.status_code} {resp.text[:160]}"
    _scan.details = details  # type: ignore[attr-defined]
    return leaks, statuses


async def test_no_get_route_returns_another_tenants_data(estate):
    leaks, statuses = await _scan(_all_permission_token(estate["user_b"]), estate)
    real = {p: n for p, n in leaks.items() if n}
    assert real == {}, f"tenant B saw tenant A data on {len(real)} route(s): {_scan.details}"
    # Every route answered (server errors would hide behaviour, so list them).
    assert statuses.get(-1, 0) == 0, f"routes raised instead of answering: {[p for p in leaks if 'raised' in p]}"
    assert sum(statuses.values()) >= 200


async def test_no_get_route_leaks_to_another_tenants_api_key(estate, monkeypatch):
    """Same scan through the API-key principal path (all scopes).

    API-key traffic is limited per client IP (60/min) and the limiter waits for
    a free slot rather than answering 429, so 228 requests from one test client
    would wait about three minutes. Rate limiting is not under test here."""
    from app.main import _RateLimiter

    async def no_wait(self, key, rpm):
        return None

    monkeypatch.setattr(_RateLimiter, "acquire", no_wait)
    leaks, statuses = await _scan(estate["key_b"], estate)
    real = {p: n for p, n in leaks.items() if n}
    assert real == {}, f"tenant B's API key saw tenant A data: {_scan.details}"
    assert statuses.get(401, 0) < sum(statuses.values()) / 2, f"API key was not accepted: {statuses}"


async def test_positive_control_owner_sees_their_own_data(estate):
    """The same scan as tenant A's own user must find the markers, otherwise
    the leak scan above proves nothing."""
    leaks, _ = await _scan(_all_permission_token(estate["user_a"]), estate)
    seen_routes = {p for p, n in leaks.items() if n}
    for expected in ("/api/devices", "/api/incidents", "/api/alerts", "/api/sops"):
        assert expected in seen_routes, f"positive control: {expected} did not return tenant A's own data"
