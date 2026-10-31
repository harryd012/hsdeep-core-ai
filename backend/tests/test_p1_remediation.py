"""
P1 Remediation v0.1 - proof tests for the five enforcement gaps.

One test per gap minimum:
  1. require_active_subscription wired on formerly-ungated write routes
     (workflows as representative; alerts/devices/settings/users share it).
  2. BundleSaver device/sensor plan-limit enforcement (pipeline data is
     block-and-audit-but-drop-silently for NEW rows; updates always pass).
  3. Seat enforcement on POST /api/auth/users.
  4. Tenant-aware rate limiting keyed off plan.api_rate_limit.
  5. Scheduler skips sources of tenants without an active subscription and
     audits the skip.

Runs against the real Postgres DB per project convention.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.collector_run import CollectorRun
from app.models.knowledge_audit import AuditEvent
from app.models.monitoring_source import MonitoringSource
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService

pytestmark = pytest.mark.asyncio


async def _seed_sub(db, tenant_id, *, status="active", device_limit=100,
                    sensor_limit=1000, seats=5, api_rate_limit=12345):
    plan = await PlanRepository(db).create(
        name=f"P1-{uuid.uuid4().hex[:8]}",
        tier=f"p1-{uuid.uuid4().hex[:8]}",
        description="p1 remediation test",
        price_monthly=0.0,
        price_annual=0.0,
        device_limit=device_limit,
        sensor_limit=sensor_limit,
        api_rate_limit=api_rate_limit,
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


async def _cleanup(tenant_id, plan_ids):
    from app.models.plan import Plan
    from app.models.subscription import Subscription

    async with AsyncSessionLocal() as db:
        await db.execute(AuditEvent.__table__.delete().where(AuditEvent.tenant_id == tenant_id))
        await db.execute(Subscription.__table__.delete().where(Subscription.tenant_id == tenant_id))
        for pid in plan_ids:
            await db.execute(Plan.__table__.delete().where(Plan.id == pid))
        await db.commit()


async def _make_user(tenant_id):
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        u = User(
            tenant_id=tenant_id,
            email=f"p1-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x",
            full_name="P1 Actor",
            is_active=True,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


def _jwt(tenant_id, permissions, user_id):
    return create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        email="p1@example.com",
        is_superuser=False,
        permissions=frozenset(permissions),
    )


async def _make_source_direct():
    """A committed source under its own throwaway tenant (no subscription)."""
    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as db:
        t = Tenant(name=f"p1-t-{uuid.uuid4().hex[:8]}", slug=f"p1-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.flush()
        s = MonitoringSource(
            tenant_id=t.id,
            name=f"p1-src-{uuid.uuid4().hex[:8]}",
            source_type="prtg",
            is_active=True,
            poll_interval_seconds=60,
            status="pending",
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s


# ---------------------------------------------------------------------------
# Gap 1: active-subscription gate on formerly-ungated write routes
# ---------------------------------------------------------------------------

class TestWriteRoutesGated:
    async def test_suspended_tenant_write_routes_402_reads_open(self, tenant):
        """POST /api/workflows -> 402 subscription_suspended while
        GET /api/workflows stays open."""
        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, status="suspended")
        user = await _make_user(tenant.id)
        try:
            headers = {
                "Authorization": "Bearer "
                + _jwt(tenant.id, {"workflows.write", "workflows.read"}, user.id)
            }
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                post = await client.post(
                    "/api/workflows", headers=headers,
                    json={"name": "p1 wf", "workflow_type": "remediation"},
                )
                get = await client.get("/api/workflows", headers=headers)
            assert post.status_code == 402, post.text
            detail = post.json()["detail"]
            assert isinstance(detail, dict)
            assert detail["error"] == "subscription_suspended"
            assert get.status_code == 200, get.text
        finally:
            await _cleanup(tenant.id, [plan.id])

    async def test_past_due_tenant_can_still_write(self, tenant):
        """past_due keeps its intentional grace period - writes pass."""
        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, status="past_due")
        user = await _make_user(tenant.id)
        try:
            headers = {
                "Authorization": "Bearer " + _jwt(tenant.id, {"workflows.write"}, user.id)
            }
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post("/api/workflows", headers=headers,
                                         json={"name": "pd wf"})
            assert resp.status_code != 402, resp.text
        finally:
            await _cleanup(tenant.id, [plan.id])


# ---------------------------------------------------------------------------
# Gap 2: BundleSaver device/sensor plan-limit enforcement
# ---------------------------------------------------------------------------

class TestBundleSaverLimits:
    async def test_new_creations_blocked_but_updates_pass(self, tenant, db):
        """Suspended tenant: NEW devices dropped (block-and-audit-drop-
        silently), updates to existing external_ids never blocked."""
        from app.collectors.base import (
            NormalizedBundle,
            NormalizedDevice,
            NormalizedSensorReading,
        )
        from app.repositories.device_repository import DeviceRepository
        from app.repositories.event_repository import EventRepository
        from app.repositories.interface_repository import InterfaceRepository
        from app.repositories.metric_repository import MetricRepository
        from app.repositories.sensor_repository import SensorRepository
        from app.services.monitoring.bundle_saver import BundleSaver

        _, plan = await _seed_sub(db, tenant.id, status="suspended")
        source = await _make_source_direct()

        plain = BundleSaver(
            DeviceRepository(db), InterfaceRepository(db), SensorRepository(db),
            MetricRepository(db), EventRepository(db),
        )
        ext = f"dev-{uuid.uuid4().hex[:8]}"
        await plain.save(source, NormalizedBundle(
            devices=[NormalizedDevice(external_id=ext, name="pre", device_type="router", status="up")],
            sensors=[],
        ))
        await db.commit()

        billing = BillingService(
            plans=PlanRepository(db), subscriptions=SubscriptionRepository(db), db=db
        )
        gated = BundleSaver(
            DeviceRepository(db), InterfaceRepository(db), SensorRepository(db),
            MetricRepository(db), EventRepository(db), billing=billing,
        )
        new_ext = f"dev-new-{uuid.uuid4().hex[:8]}"
        result = await gated.save(source, NormalizedBundle(
            devices=[
                NormalizedDevice(external_id=ext, name="renamed", device_type="router", status="down"),
                NormalizedDevice(external_id=new_ext, name="new", device_type="switch", status="up"),
            ],
            sensors=[NormalizedSensorReading(
                external_id=f"sns-{uuid.uuid4().hex[:8]}", name="cpu",
                metric_type="cpu_utilization", value=1.0, unit="%",
                device_external_id=None,
            )],
        ))
        await db.commit()

        assert result.devices_blocked == 1
        dev = await DeviceRepository(db).get_by_external_id(source.id, ext)
        assert dev is not None and dev.name == "renamed"
        assert await DeviceRepository(db).get_by_external_id(source.id, new_ext) is None

        await _cleanup(tenant.id, [plan.id])


# ---------------------------------------------------------------------------
# Gap 3: seats enforced on POST /api/auth/users
# ---------------------------------------------------------------------------

class TestSeatsEnforced:
    async def test_invite_blocked_at_seat_limit(self, tenant):
        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, seats=0)
        actor = await _make_user(tenant.id)
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post(
                    "/api/auth/users",
                    headers={"Authorization": "Bearer " + _jwt(tenant.id, {"users.create"}, actor.id)},
                    json={"email": f"inv-{uuid.uuid4().hex[:6]}@example.com",
                          "password": "Str0ngPass!123"},
                )
            assert resp.status_code == 402, resp.text
            body = resp.json()
            detail = body.get("detail", body)
            assert detail.get("error") == "plan_limit_exceeded"
            assert detail.get("resource") == "seats"
        finally:
            await _cleanup(tenant.id, [plan.id])


# ---------------------------------------------------------------------------
# Gap 4: authenticated (session) rate limiting scoped per user, not per IP.
# plan.api_rate_limit is a per-billing-period allowance (test_api_quota.py)
# and must not be reused as a per-minute ceiling (test_session_rate_cap.py).
# ---------------------------------------------------------------------------

class TestTenantRateLimitScope:
    async def test_authenticated_requests_keyed_to_user_cap(self, tenant):
        """Middleware keys session traffic per user and applies the session
        fair-use cap, not the tenant plan's period allowance."""
        from app.main import _RateLimiter

        async with AsyncSessionLocal() as db:
            _, plan = await _seed_sub(db, tenant.id, api_rate_limit=12345)

        limiter = _RateLimiter(requests_per_minute=60, session_rpm_per_user=600)
        user_id = uuid.uuid4()
        token = _jwt(tenant.id, set(), user_id)

        class _FakeRequest:
            def __init__(self, headers):
                self.headers = headers
                self.client = None

        req = _FakeRequest({"authorization": f"Bearer {token}"})
        key = limiter.key_for_request(req)
        assert key == f"user:{tenant.id}:{user_id}"
        rpm = await limiter.rpm_for(key)
        assert rpm == 600, f"expected session cap 600, got {rpm}"

        anon_key = limiter.key_for_request(_FakeRequest({}))
        assert anon_key.startswith("ip:")
        assert await limiter.rpm_for(anon_key) == 60

        await _cleanup(tenant.id, [plan.id])


# ---------------------------------------------------------------------------
# Gap 5: scheduler skips sources of tenants without an active subscription
# ---------------------------------------------------------------------------

class TestSchedulerSubscriptionFilter:
    async def test_due_source_without_subscription_skipped_and_audited(self):
        from sqlalchemy import select

        from app.models.tenant import Tenant
        from app.scheduler.jobs import _run_due_sources

        source = await _make_source_direct()
        try:
            async with AsyncSessionLocal() as db:
                src = await db.get(MonitoringSource, source.id)
                src.next_run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await db.commit()

            await _run_due_sources()

            async with AsyncSessionLocal() as db:
                runs = (
                    await db.execute(
                        select(CollectorRun).where(CollectorRun.monitoring_source_id == source.id)
                    )
                ).scalars().all()
                assert runs == [], "subscription-less tenant must not be collected"

                audits = (
                    await db.execute(
                        select(AuditEvent).where(
                            AuditEvent.tenant_id == source.tenant_id,
                            AuditEvent.action == "collection.skipped_no_subscription",
                        )
                    )
                ).scalars().all()
                assert len(audits) >= 1, "skip must be audit-logged"
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(Tenant.__table__.delete().where(Tenant.id == source.tenant_id))
                await db.commit()
