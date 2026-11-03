"""Scheduled discovery (observability plan P2).

Schedule parsing; the scope API validating and normalizing schedules, setting
the next run, auditing changes and staying tenant-scoped; "probe now" and the
scheduler job sharing one path (durable job with its trigger, next run moved
on); and the job skipping disabled scopes and tenants without a subscription.
The network probe itself is stubbed: these tests never open a socket.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.discovery_probe import DiscoveryProbeJob
from app.models.discovery_scope import DiscoveryScope
from app.models.knowledge_audit import AuditEvent
from app.models.tenant import Tenant
from app.scheduler.discovery_jobs import run_due_scopes
from app.services.discovery.network_probe import HostProbeResult, NetworkProbeReport
from app.services.discovery.schedule import ScheduleInvalid, next_run_after, normalize_schedule, parse_schedule

WRITER = frozenset({"devices.read", "devices.write"})


@pytest.fixture
def stub_probe(monkeypatch):
    """Replace the network probe; `calls` lists the scopes probed, `fail` makes it raise."""
    state = {"calls": [], "fail": False}

    async def fake(scope):
        state["calls"].append(scope.id)
        if state["fail"]:
            raise RuntimeError("probe exploded")
        return NetworkProbeReport(
            cidr=scope.target, hosts_probed=2, hosts_total=2,
            hosts=[HostProbeResult(address="10.9.0.1", reachable=True, latency_ms=1.5), HostProbeResult(address="10.9.0.2")],
        )

    monkeypatch.setattr("app.services.discovery.scope_probe_service.probe_scope", fake)
    return state


def _client(tenant_id, permissions=WRITER):
    token = create_access_token(
        user_id=uuid.UUID(int=0), tenant_id=tenant_id, email="noc@hsdeep.local", is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


BASE = "/api/v1/infrastructure/discovery/scopes"


async def _base(client) -> str:
    """The scopes route prefix as mounted (with or without /v1)."""
    for prefix in (BASE, BASE.replace("/v1", "")):
        if (await client.get(prefix)).status_code != 404:
            return prefix
    raise AssertionError("discovery scopes route not mounted")


async def _scope(tenant_id, **fields) -> DiscoveryScope:
    async with AsyncSessionLocal() as db:
        scope = DiscoveryScope(
            tenant_id=tenant_id, name=fields.pop("name", f"scope-{uuid.uuid4().hex[:6]}"),
            scope_type="private_cidr", target="10.9.0.0/30", allowed_protocols=[], **fields,
        )
        db.add(scope)
        await db.commit()
        await db.refresh(scope)
        return scope


async def _get(scope_id) -> DiscoveryScope:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(DiscoveryScope).where(DiscoveryScope.id == scope_id))).scalar_one()


async def _jobs(scope_id) -> list[DiscoveryProbeJob]:
    async with AsyncSessionLocal() as db:
        return list((await db.execute(select(DiscoveryProbeJob).where(DiscoveryProbeJob.scope_id == scope_id))).scalars())


# ---------------------------------------------------------------------------
# Schedule (pure)
# ---------------------------------------------------------------------------


def test_schedule_parsing_and_bounds():
    assert parse_schedule("15m") == timedelta(minutes=15)
    assert parse_schedule(" 6H ") == timedelta(hours=6)
    assert parse_schedule("30d") == timedelta(days=30)
    assert normalize_schedule(" 1 D") == "1d"
    assert normalize_schedule("") is None and normalize_schedule(None) is None
    for bad in ("daily", "5m", "31d", "0h", "*/5 * * * *", "1w"):
        with pytest.raises(ScheduleInvalid):
            parse_schedule(bad)
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert next_run_after("1h", t) == t + timedelta(hours=1)
    assert next_run_after(None, t) is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


async def test_scope_api_schedule_audit_and_isolation(tenant, stub_probe):
    async with _client(tenant.id) as client:
        base = await _base(client)
        r = await client.post(base, json={"name": "Lab", "scope_type": "private_cidr", "target": "10.9.0.0/30", "schedule": "6H"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["schedule"] == "6h"
        next_run = datetime.fromisoformat(body["next_run_at"])
        assert timedelta(hours=5, minutes=59) < next_run - datetime.now(timezone.utc) <= timedelta(hours=6)
        sid = body["id"]

        assert (await client.post(base, json={"name": "Bad", "scope_type": "private_cidr", "target": "10.9.0.0/30", "schedule": "daily"})).status_code == 422
        assert (await client.patch(f"{base}/{sid}", json={"schedule": "5m"})).status_code == 422

        # Unchanged schedule keeps its slot; a new one restarts the interval; clearing removes it.
        same = (await client.patch(f"{base}/{sid}", json={"schedule": "6h"})).json()
        assert same["next_run_at"] == body["next_run_at"]
        daily = (await client.patch(f"{base}/{sid}", json={"schedule": "1d"})).json()
        assert datetime.fromisoformat(daily["next_run_at"]) - datetime.now(timezone.utc) > timedelta(hours=23)
        off = (await client.patch(f"{base}/{sid}", json={"schedule": None})).json()
        assert (off["schedule"], off["next_run_at"]) == (None, None)

        # Probe now: a manual job, the scope's run state, evidence returned.
        await client.patch(f"{base}/{sid}", json={"schedule": "1h"})
        probe = await client.post(f"{base}/{sid}/probe")
        assert probe.status_code == 200, probe.text
        assert (probe.json()["reachable"], probe.json()["hosts_probed"]) == (1, 2)
        scope = (await client.get(f"{base}/{sid}")).json()
        assert scope["status"] == "ok" and scope["last_run_at"] and scope["next_run_at"]

        stub_probe["fail"] = True
        assert (await client.post(f"{base}/{sid}/probe")).status_code == 502
        failed = (await client.get(f"{base}/{sid}")).json()
        assert (failed["status"], failed["last_error"]) == ("error", "probe exploded")

        # Read-only callers cannot change scopes.
    async with _client(tenant.id, permissions=frozenset({"devices.read"})) as client:
        assert (await client.patch(f"{base}/{sid}", json={"schedule": "1h"})).status_code == 403

    # Another tenant sees nothing.
    async with _client(uuid.uuid4()) as client:
        assert (await client.get(f"{base}/{sid}")).status_code == 404
        assert (await client.patch(f"{base}/{sid}", json={"schedule": "1h"})).status_code == 404

    jobs = await _jobs(uuid.UUID(sid))
    assert sorted((j.trigger, j.status) for j in jobs) == [("manual", "completed"), ("manual", "failed")]

    async with AsyncSessionLocal() as db:
        events = (
            await db.execute(
                select(AuditEvent).where(AuditEvent.tenant_id == tenant.id, AuditEvent.entity_type == "discovery_scope")
                .order_by(AuditEvent.created_at)
            )
        ).scalars().all()
    actions = [e.action for e in events]
    # 1d, cleared, 1h; re-sending the same 6h changed nothing and is not audited.
    assert actions[0] == "discovery_scope.created" and actions.count("discovery_scope.updated") == 3
    assert events[1].old_values == {"schedule": "6h"} and events[1].new_values == {"schedule": "1d"}

    async with _client(tenant.id) as client:
        assert (await client.delete(f"{base}/{sid}")).status_code == 204
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(AuditEvent).where(AuditEvent.entity_id == uuid.UUID(sid), AuditEvent.action == "discovery_scope.deleted"))).scalar_one()


async def test_reenabling_a_scope_restarts_its_interval(tenant):
    past = datetime.now(timezone.utc) - timedelta(days=3)
    scope = await _scope(tenant.id, schedule="1h", next_run_at=past, enabled=False)
    async with _client(tenant.id) as client:
        body = (await client.patch(f"{await _base(client)}/{scope.id}", json={"enabled": True})).json()
    assert datetime.fromisoformat(body["next_run_at"]) > datetime.now(timezone.utc) + timedelta(minutes=59)


# ---------------------------------------------------------------------------
# Scheduler job
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("seeded_subscription")
async def test_scheduler_probes_due_scopes_only(tenant, stub_probe):
    now = datetime.now(timezone.utc)
    due = await _scope(tenant.id, schedule="1h", next_run_at=now - timedelta(minutes=1))
    later = await _scope(tenant.id, schedule="1h", next_run_at=now + timedelta(minutes=30))
    disabled = await _scope(tenant.id, schedule="1h", next_run_at=now - timedelta(minutes=1), enabled=False)
    unscheduled = await _scope(tenant.id)
    cloud = await _scope(tenant.id, schedule="1h", next_run_at=now - timedelta(minutes=1))
    async with AsyncSessionLocal() as db:  # a type the network probe cannot run
        (await db.execute(select(DiscoveryScope).where(DiscoveryScope.id == cloud.id))).scalar_one().scope_type = "cloud_account"
        await db.commit()

    assert await run_due_scopes(now) == 1
    assert stub_probe["calls"] == [due.id]
    jobs = await _jobs(due.id)
    assert [(j.trigger, j.status, j.reachable) for j in jobs] == [("scheduler", "completed", 1)]
    after = await _get(due.id)
    assert after.status == "ok" and after.next_run_at == now + timedelta(hours=1)
    for other in (later, disabled, unscheduled, cloud):
        assert await _jobs(other.id) == []

    # A failing probe is recorded and waits for its next slot instead of retrying every tick.
    stub_probe["fail"] = True
    async with AsyncSessionLocal() as db:
        (await db.execute(select(DiscoveryScope).where(DiscoveryScope.id == due.id))).scalar_one().next_run_at = now
        await db.commit()
    assert await run_due_scopes(now + timedelta(seconds=1)) == 1
    failed = await _get(due.id)
    assert failed.status == "error" and failed.next_run_at == now + timedelta(seconds=1) + timedelta(hours=1)
    assert sorted(j.status for j in await _jobs(due.id)) == ["completed", "failed"]
    assert await run_due_scopes(now + timedelta(seconds=2)) == 0


async def test_scheduler_skips_tenant_without_subscription(stub_probe):
    async with AsyncSessionLocal() as db:
        lapsed = Tenant(name="Lapsed", slug=f"lapsed-{uuid.uuid4().hex[:8]}")
        db.add(lapsed)
        await db.commit()
        await db.refresh(lapsed)
    try:
        now = datetime.now(timezone.utc)
        scope = await _scope(lapsed.id, schedule="1h", next_run_at=now - timedelta(minutes=1))
        assert await run_due_scopes(now) == 0
        assert stub_probe["calls"] == []
        assert (await _get(scope.id)).next_run_at == now + timedelta(hours=1)
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == lapsed.id))
            await db.commit()
