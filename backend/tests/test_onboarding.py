"""
Customer Onboarding & Activation v0.1 tests (Phase 2).

Covers stage derivation from live server-side state:
  * not_started        - no subscription
  * subscription_ready - trial subscription, no sources
  * source_configured  - source exists, no successful collection
  * activated          - source with last_successful_run_at set

Tenant isolation: another tenant's sources must NOT affect this tenant's
onboarding status.

Runs against the real configured Postgres database (see conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.api.routes.onboarding import get_onboarding_status
from app.core.db import AsyncSessionLocal
from app.models.tenant import Tenant
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService

pytestmark = pytest.mark.asyncio


class _FakePrincipal:
    user_id = None
    email = "tester@example.com"


async def _status(tenant_id: uuid.UUID):
    async with AsyncSessionLocal() as db:
        billing = BillingService(
            plans=PlanRepository(db),
            subscriptions=SubscriptionRepository(db),
            db=db,
        )
        return await get_onboarding_status(
            tenant_id=tenant_id,
            _=_FakePrincipal(),
            billing=billing,
            db=db,
        )


async def _ensure_plan():
    async with AsyncSessionLocal() as db:
        repo = PlanRepository(db)
        plan = await repo.get_by_tier("starter")
        if plan is None:
            plan = await repo.create(
                name="Starter", tier="starter", description=None,
                price_monthly=0, price_annual=0, device_limit=25,
                sensor_limit=500, api_rate_limit=1000, collector_limit=2,
                seats=3, features_json={}, is_public=True,
            )
            await db.commit()
            await db.refresh(plan)
        return plan


class TestOnboardingStages:

    async def test_not_started_without_subscription(self, tenant):
        out = await _status(tenant.id)
        assert out.stage == "not_started"
        assert out.activated is False
        assert out.subscription_status is None

    async def test_subscription_ready_with_trial(self, tenant):
        plan = await _ensure_plan()
        async with AsyncSessionLocal() as db:
            await SubscriptionRepository(db).create(
                tenant_id=tenant.id, plan_id=plan.id, status="trialing"
            )
            await db.commit()

        out = await _status(tenant.id)
        assert out.stage == "subscription_ready"
        assert out.subscription_status == "trialing"
        assert out.sources_configured == 0

    async def test_source_configured_before_first_collection(self, tenant):
        plan = await _ensure_plan()
        async with AsyncSessionLocal() as db:
            await SubscriptionRepository(db).create(
                tenant_id=tenant.id, plan_id=plan.id, status="trialing"
            )
            await MonitoringSourceRepository(db).create(
                tenant_id=tenant.id, name=f"src-{uuid.uuid4().hex[:6]}",
                source_type="prtg",
            )
            await db.commit()

        out = await _status(tenant.id)
        assert out.stage == "source_configured"
        assert out.sources_configured == 1
        assert out.sources_connected == 0
        assert out.first_collection_at is None

    async def test_activated_after_successful_collection(self, tenant):
        plan = await _ensure_plan()
        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as db:
            await SubscriptionRepository(db).create(
                tenant_id=tenant.id, plan_id=plan.id, status="active"
            )
            src = await MonitoringSourceRepository(db).create(
                tenant_id=tenant.id, name=f"src-{uuid.uuid4().hex[:6]}",
                source_type="prtg",
            )
            # Simulate what the collection orchestrator records on success.
            src.last_successful_run_at = now
            src.consecutive_successes = 1
            await db.commit()

        out = await _status(tenant.id)
        assert out.stage == "activated"
        assert out.activated is True
        assert out.first_collection_at is not None


class TestOnboardingTenantIsolation:

    async def test_other_tenant_sources_do_not_leak(self, tenant):
        """Sources belonging to a DIFFERENT tenant must not count toward
        this tenant's onboarding progress."""
        other_id = None
        try:
            async with AsyncSessionLocal() as db:
                other = Tenant(
                    name="Other Org", slug=f"other-{uuid.uuid4().hex[:8]}", is_active=True
                )
                db.add(other)
                await db.flush()
                other_id = other.id
                src = await MonitoringSourceRepository(db).create(
                    tenant_id=other.id, name=f"other-src-{uuid.uuid4().hex[:6]}",
                    source_type="prtg",
                )
                src.last_successful_run_at = datetime.now(timezone.utc)
                await db.commit()

            # This tenant has NO subscription and NO sources.
            out = await _status(tenant.id)
            assert out.stage == "not_started"
            assert out.sources_configured == 0
            assert out.first_collection_at is None
        finally:
            if other_id is not None:
                async with AsyncSessionLocal() as db:
                    t = await db.get(Tenant, other_id)
                    if t is not None:
                        await db.delete(t)
                        await db.commit()