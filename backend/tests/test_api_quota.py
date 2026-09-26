"""
API-request quota tests (``Plan.api_rate_limit``).

Regression suite for the defect where the plan's API-request allowance was
declared, priced, displayed in the Usage UI, and never enforceable:

  * ``app/services/ingester/pipeline.py``'s ``count_api_call()`` had zero
    callers, so ``api.call`` usage was always 0 (live evidence:
    ``{"resource":"api_calls","limit":50000,"used":0}``).
  * The request limiter applied ``api_rate_limit`` as a per-MINUTE burst
    ceiling instead of the per-billing-period quota it actually is, so the
    monthly allowance could never trip.

Runs against the real Postgres DB per project convention (conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.knowledge_audit import AuditEvent
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.usage import UsageCounter, EVENT_TYPE_API_CALL
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.metering import api_quota

pytestmark = pytest.mark.asyncio

UNLIMITED = 0  # Plan documents 0 as "no metered allowance configured"


async def _seed_tenant_with_plan(*, api_rate_limit: int, with_subscription: bool = True):
    """A real committed Tenant + Plan (+ Subscription). Returns (tenant, plan_ids)."""
    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="API Quota Test", slug=f"quota-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        plan = await PlanRepository(db).create(
            name=f"AQ-{uuid.uuid4().hex[:8]}",
            tier=f"aq-{uuid.uuid4().hex[:8]}",
            description="api quota test",
            price_monthly=0.0,
            price_annual=0.0,
            device_limit=100,
            sensor_limit=1000,
            api_rate_limit=api_rate_limit,
            collector_limit=10,
            seats=5,
            features_json={},
            is_public=False,
        )
        plan_ids = [plan.id]

        if with_subscription:
            now = datetime.now(timezone.utc)
            await SubscriptionRepository(db).create(
                tenant_id=tenant.id,
                plan_id=plan.id,
                status="active",
                # UsageCounter buckets are truncated to the UTC hour and
                # get_entitlements sums them with `bucket_start >=
                # current_period_start`, so a period starting mid-hour would
                # exclude the current bucket. Real subscriptions start on a past
                # date; start this one an hour back so the current bucket counts.
                current_period_start=now - timedelta(hours=1),
                current_period_end=now + timedelta(days=30),
                billing_interval="month",
            )
        await db.commit()
    return tenant, plan_ids


async def _cleanup(tenant_id: uuid.UUID, plan_ids: list[uuid.UUID]) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(UsageCounter.__table__.delete().where(UsageCounter.tenant_id == tenant_id))
        await db.execute(AuditEvent.__table__.delete().where(AuditEvent.tenant_id == tenant_id))
        await db.execute(Subscription.__table__.delete().where(Subscription.tenant_id == tenant_id))
        for pid in plan_ids:
            await db.execute(Plan.__table__.delete().where(Plan.id == pid))
        from app.models.tenant import Tenant

        await db.execute(Tenant.__table__.delete().where(Tenant.id == tenant_id))
        await db.commit()


async def _actor_user(tenant_id: uuid.UUID):
    from app.models.user import User

    async with AsyncSessionLocal() as db:
        u = User(
            tenant_id=tenant_id,
            email=f"quota-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="test-hash",
            full_name="Quota Test Actor",
            is_active=True,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


def _jwt(tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        email="quota-test@example.com",
        is_superuser=False,
        permissions=frozenset({"tasks.read"}),
    )


@pytest.fixture(autouse=True)
def _clean_module_state():
    """The meter and allowance cache are in-process, so isolate every test."""
    api_quota.reset_state()
    yield
    api_quota.reset_state()


async def _persisted_api_calls(tenant_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.coalesce(func.sum(UsageCounter.count), 0)).where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.event_type == EVENT_TYPE_API_CALL,
            )
        )
        return int(result.scalar_one())


class TestApiQuotaMetering:
    """The meter that had no callers must now persist real counts."""

    async def test_flush_persists_api_call_usage(self):
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1000)
        try:
            assert await _persisted_api_calls(tenant.id) == 0

            await api_quota.record_api_call(tenant.id, 5)
            assert api_quota.pending_for(tenant.id) == 5
            await api_quota.flush_api_calls(tenant.id)

            assert api_quota.pending_for(tenant.id) == 0
            assert await _persisted_api_calls(tenant.id) == 5
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_allowance_sees_persisted_usage(self):
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1000)
        try:
            await api_quota.record_api_call(tenant.id, 7)
            await api_quota.flush_api_calls(tenant.id)

            limit, used, over = await api_quota.api_allowance(tenant.id)
            assert limit == 1000
            assert used == 7
            assert over is False
        finally:
            await _cleanup(tenant.id, plan_ids)


class TestApiQuotaEnforcement:
    """The plan allowance must be enforced per billing period."""

    async def test_no_subscription_never_blocks(self):
        tenant, plan_ids = await _seed_tenant_with_plan(
            api_rate_limit=5, with_subscription=False
        )
        try:
            limit, _used, over = await api_quota.api_allowance(tenant.id)
            assert limit is None, "a tenant with no subscription must not be quota-blocked"
            assert over is False
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_zero_limit_means_not_enforced(self):
        """Plan documents 0 as 'no metered allowance configured'."""
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=UNLIMITED)
        try:
            limit, _used, over = await api_quota.api_allowance(tenant.id)
            assert limit is None
            assert over is False
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_request_within_quota_is_served(self):
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1000)
        try:
            user = await _actor_user(tenant.id)
            api_quota.reset_state()
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    "/api/tasks/counts",
                    headers={"Authorization": f"Bearer {_jwt(tenant.id, user.id)}"},
                )
            assert resp.status_code == 200, resp.text
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_request_over_quota_is_rejected_429(self):
        """A tenant past its period allowance must be refused, not just slowed."""
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1)
        try:
            user = await _actor_user(tenant.id)

            # Real persisted usage already exceeds the plan allowance.
            await api_quota.record_api_call(tenant.id, 3)
            await api_quota.flush_api_calls(tenant.id)
            api_quota.reset_state()  # force a fresh allowance read

            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    "/api/tasks/counts",
                    headers={"Authorization": f"Bearer {_jwt(tenant.id, user.id)}"},
                )

            assert resp.status_code == 429, resp.text
            body = resp.json()
            assert body["error"] == "plan_limit_exceeded"
            assert body["resource"] == "api_calls"
            assert body["limit"] == 1
            assert body["used"] >= 3
            assert body["upgrade_url"] == "/usage"
            # No credentials or connection strings may leak.
            blob = resp.text.lower()
            assert "password" not in blob
            assert "redis://" not in blob
            assert "postgresql" not in blob
        finally:
            await _cleanup(tenant.id, plan_ids)
