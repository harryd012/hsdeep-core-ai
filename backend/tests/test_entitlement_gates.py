"""
Entitlement & subscription gate tests (sub-task 3).

Covers:
  1. Suspended tenant: BundleSaver blocks NEW device AND sensor creation
     gracefully (never raises - Property 8), and write routes return
     402 "subscription_suspended" while GET routes stay open.
  2. Tenant at device_limit / sensor_limit: BundleSaver blocks creation
     of that resource only; the other resource is unaffected.
  3. Active tenant: devices/sensors upsert normally.
  4. Seats: POST /auth/users (invite/create) returns 402 when the tenant
     is at its seat limit; succeeds when seats are available.

Runs against the real Postgres DB per project convention (conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.collectors.base import NormalizedBundle, NormalizedDevice, NormalizedSensorReading
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.knowledge_audit import AuditEvent
from app.models.monitoring_source import MonitoringSource
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.sensor_repository import SensorRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService
from app.services.monitoring.bundle_saver import BundleSaver

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_billing(db):
    return BillingService(
        plans=PlanRepository(db),
        subscriptions=SubscriptionRepository(db),
        db=db,
    )


async def _seed_sub(
    db,
    tenant_id: uuid.UUID,
    *,
    status: str = "active",
    device_limit: int = 100,
    sensor_limit: int = 1000,
    seats: int = 5,
):
    plan = await PlanRepository(db).create(
        name=f"EG-{uuid.uuid4().hex[:8]}",
        tier=f"eg-{uuid.uuid4().hex[:8]}",
        description="entitlement-gate test",
        price_monthly=0.0,
        price_annual=0.0,
        device_limit=device_limit,
        sensor_limit=sensor_limit,
        api_rate_limit=100000,
        collector_limit=10,
        seats=seats,
        features_json={},
        is_public=False,
    )
    now = datetime.now(timezone.utc)
    sub = await SubscriptionRepository(db).create(
        tenant_id=tenant_id,
        plan_id=plan.id,
        status=status,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        billing_interval="month",
    )
    await db.commit()
    return sub, plan


async def _cleanup(tenant_id: uuid.UUID, plan_ids: list[uuid.UUID]) -> None:
    async with AsyncSessionLocal() as db:
        from app.models.user import User

        await db.execute(AuditEvent.__table__.delete().where(AuditEvent.tenant_id == tenant_id))
        await db.execute(User.__table__.delete().where(User.tenant_id == tenant_id))
        await db.execute(Subscription.__table__.delete().where(Subscription.tenant_id == tenant_id))
        for pid in plan_ids:
            await db.execute(Plan.__table__.delete().where(Plan.id == pid))
        await db.commit()


async def _make_actor_user(tenant_id: uuid.UUID) -> "User":
    """A real committed User row for the JWT actor - audit_events.actor_id
    has an FK to users, so the acting principal must exist."""
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        u = User(
            tenant_id=tenant_id,
            email=f"actor-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="test-hash",
            full_name="Gate Test Actor",
            is_active=True,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


def _jwt(tenant_id: uuid.UUID, permissions: set[str], user_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        email="gate-test@example.com",
        is_superuser=False,
        permissions=frozenset(permissions),
    )


def _bundle() -> NormalizedBundle:
    return NormalizedBundle(
        devices=[
            NormalizedDevice(
                external_id=f"dev-{uuid.uuid4().hex[:8]}",
                name="Gate Test Router",
                device_type="router",
                status="up",
            )
        ],
        sensors=[
            NormalizedSensorReading(
                external_id=f"sns-{uuid.uuid4().hex[:8]}",
                name="cpu",
                metric_type="cpu_utilization",
                value=42.0,
                unit="%",
                device_external_id=None,
            )
        ],
    )


async def _make_source(tenant_id: uuid.UUID) -> MonitoringSource:
    async with AsyncSessionLocal() as db:
        source = MonitoringSource(
            tenant_id=tenant_id,
            name=f"eg-src-{uuid.uuid4().hex[:8]}",
            source_type="prtg",
            is_active=True,
            poll_interval_seconds=3600,
            status="pending",
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)
        return source


def _saver(db, billing) -> BundleSaver:
    return BundleSaver(
        device_repo=DeviceRepository(db),
        interface_repo=InterfaceRepository(db),
        sensor_repo=SensorRepository(db),
        metric_repo=MetricRepository(db),
        event_repo=EventRepository(db),
        billing=billing,
    )


# ---------------------------------------------------------------------------
# 1 + 2 + 3: BundleSaver device/sensor entitlement gates
# ---------------------------------------------------------------------------

class TestBundleSaverEntitlementGates:
    async def test_suspended_tenant_blocked_from_creating_device_and_sensor(
        self, tenant, db
    ):
        """A suspended tenant's ingestion must not create new devices or
        sensors - and save() must NOT raise (Property 8)."""
        sub, plan = await _seed_sub(db, tenant.id, status="suspended")
        source = await _make_source(tenant.id)
        try:
            result = await _saver(db, await _make_billing(db)).save(source, _bundle())
            assert result.devices_upserted == 0
            assert result.devices_blocked == 1
            assert result.sensors_created == 0
            assert result.sensors_blocked == 1
        finally:
            await _cleanup(tenant.id, [plan.id])

    async def test_tenant_at_device_limit_blocked_but_sensors_ok(self, tenant, db):
        """device_limit=0 means any device creation is blocked; sensors
        (well under their limit) are still created."""
        sub, plan = await _seed_sub(db, tenant.id, device_limit=0, sensor_limit=1000)
        source = await _make_source(tenant.id)
        try:
            result = await _saver(db, await _make_billing(db)).save(source, _bundle())
            assert result.devices_upserted == 0
            assert result.devices_blocked == 1
            assert result.sensors_created == 1
            assert result.sensors_blocked == 0
        finally:
            await _cleanup(tenant.id, [plan.id])

    async def test_tenant_at_sensor_limit_blocked_but_devices_ok(self, tenant, db):
        sub, plan = await _seed_sub(db, tenant.id, device_limit=100, sensor_limit=0)
        source = await _make_source(tenant.id)
        try:
            result = await _saver(db, await _make_billing(db)).save(source, _bundle())
            assert result.devices_upserted == 1
            assert result.devices_blocked == 0
            assert result.sensors_created == 0
            assert result.sensors_blocked == 1
        finally:
            await _cleanup(tenant.id, [plan.id])

    async def test_active_tenant_unaffected(self, tenant, db):
        sub, plan = await _seed_sub(db, tenant.id, status="active")
        source = await _make_source(tenant.id)
        try:
            result = await _saver(db, await _make_billing(db)).save(source, _bundle())
            assert result.devices_upserted == 1
            assert result.devices_blocked == 0
            assert result.sensors_created == 1
            assert result.sensors_blocked == 0
        finally:
            await _cleanup(tenant.id, [plan.id])


# ---------------------------------------------------------------------------
# 4: Seats gate on the user invite/creation endpoint
# ---------------------------------------------------------------------------

class TestSeatsGate:
    async def test_user_creation_blocked_at_seat_limit(self, tenant):
        """Plan with seats=0: the single admin already exhausts capacity ->
        POST /auth/users must 402 with plan_limit_exceeded."""
        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, seats=0)
        actor = await _make_actor_user(tenant.id)
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post(
                    "/api/auth/users",
                    headers={
                        "Authorization": "Bearer "
                        + _jwt(tenant.id, {"users.create", "users.read"}, actor.id)
                    },
                    json={
                        "email": f"invited-{uuid.uuid4().hex[:6]}@example.com",
                        "password": "Str0ngPass!123",
                    },
                )
            assert resp.status_code == 402, resp.text
            body = resp.json()
            detail = body.get("detail", body)
            assert detail.get("error") == "plan_limit_exceeded"
            assert detail.get("resource") == "seats"
        finally:
            await _cleanup(tenant.id, [plan.id])

    async def test_user_creation_allowed_with_available_seats(self, tenant):
        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, seats=5)
        actor = await _make_actor_user(tenant.id)
        try:
            email = f"invited-{uuid.uuid4().hex[:6]}@example.com"
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post(
                    "/api/auth/users",
                    headers={
                        "Authorization": "Bearer "
                        + _jwt(tenant.id, {"users.create", "users.read"}, actor.id)
                    },
                    json={"email": email, "password": "Str0ngPass!123"},
                )
            assert resp.status_code == 201, resp.text
            assert resp.json()["email"] == email
        finally:
            await _cleanup(tenant.id, [plan.id])


# ---------------------------------------------------------------------------
# require_active_subscription on write routes (tickets as representative;
# alerts/incidents/workflows use the identical shared dependency)
# ---------------------------------------------------------------------------

class TestActiveSubscriptionWriteGate:
    async def test_suspended_tenant_write_route_402_read_route_ok(self, tenant):
        """POST /api/tickets -> 402 subscription_suspended;
        GET /api/tickets stays open so the tenant can view data/billing."""
        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, status="suspended")
        actor = await _make_actor_user(tenant.id)
        try:
            headers = {
                "Authorization": "Bearer "
                + _jwt(tenant.id, {"tickets.write"}, actor.id)
            }
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                post = await client.post(
                    "/api/tickets/", headers=headers, json={"title": "x"}
                )
                get = await client.get(
                    "/api/tickets/",
                    headers={
                        "Authorization": "Bearer "
                        + _jwt(tenant.id, {"tickets.read"}, actor.id)
                    },
                )
            assert post.status_code == 402, post.text
            detail = post.json().get("detail", {})
            assert detail.get("error") == "subscription_suspended"
            assert get.status_code == 200, get.text
        finally:
            await _cleanup(tenant.id, [plan.id])

    async def test_past_due_tenant_not_gated(self, tenant):
        """past_due is an intentional grace period - writes must pass the
        guard (a 422 validation error proves the guard did NOT fire)."""
        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, status="past_due")
        actor = await _make_actor_user(tenant.id)
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post(
                    "/api/tickets/",
                    headers={
                        "Authorization": "Bearer "
                        + _jwt(tenant.id, {"tickets.write"}, actor.id)
                    },
                    json={},
                )
            assert resp.status_code != 402, (
                f"past_due tenants must not be gated, got {resp.status_code}: {resp.text}"
            )
        finally:
            await _cleanup(tenant.id, [plan.id])