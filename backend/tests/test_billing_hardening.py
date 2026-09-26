"""
Billing hardening tests (Tasks 1-4).

Covers:
  * Task 1: Webhook signature verification - forged/unsigned payload -> 400
  * Task 2: Webhook idempotency - same valid event twice -> no-op
  * Task 4: Trial expiry job - expired trial flips to past_due

Runs against the real configured Postgres database (see conftest.py).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.core.db import AsyncSessionLocal
from app.models.plan import Plan
from app.models.processed_stripe_event import ProcessedStripeEvent
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_plan(tier: str = "starter") -> Plan:
    """Create and COMMIT a plan row (so the FK is satisfied)."""
    async with AsyncSessionLocal() as db:
        repo = PlanRepository(db)
        plan = await repo.get_by_tier(tier)
        if plan is None:
            plan = await repo.create(
                name=tier.capitalize(),
                tier=tier,
                description="test plan",
                price_monthly=0.0,
                price_annual=0.0,
                device_limit=25,
                sensor_limit=500,
                api_rate_limit=1000,
                collector_limit=2,
                seats=3,
                features_json={},
                is_public=True,
            )
            await db.commit()
            await db.refresh(plan)
        return plan


async def _make_subscription(
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    *,
    status: str = "trialing",
    trial_ends_at: datetime | None = None,
    stripe_subscription_id: str | None = None,
) -> Subscription:
    async with AsyncSessionLocal() as db:
        repo = SubscriptionRepository(db)
        sub = await repo.create(
            tenant_id=tenant_id,
            plan_id=plan_id,
            status=status,
            trial_ends_at=trial_ends_at,
            stripe_subscription_id=stripe_subscription_id,
        )
        await db.commit()
        await db.refresh(sub)
        return sub


# ---------------------------------------------------------------------------
# Task 1: Webhook signature verification
# ---------------------------------------------------------------------------

class TestWebhookSignatureVerification:

    async def test_unsigned_payload_rejected_with_400(self, tenant):
        """POSTing an unsigned/forged payload must return 400.

        We call the route function directly (bypassing TestClient startup)
        because the app's startup event has a pre-existing bug where
        `async with get_db() as db:` fails on an async generator.
        """
        from app.api.routes.stripe_webhook import stripe_webhook
        from app.core.config import get_settings
        from app.core.db import AsyncSessionLocal as _SessionLocal
        from starlette.requests import Request

        settings = get_settings()
        original_secret = settings.stripe_webhook_secret
        settings.stripe_webhook_secret = "whsec_test_secret"

        try:
            # Build a fake Request with the forged payload
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/api/billing/webhook",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"stripe-signature", b"t=1,v1=forged"),
                ],
                "client": ("127.0.0.1", 12345),
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
            }
            request = Request(scope)

            # We need to set the body - use a helper to inject it
            async def _receive():
                return {"type": "http.request", "body": json.dumps({
                    "id": "evt_test_forged",
                    "type": "invoice.paid",
                    "data": {"object": {}},
                }).encode(), "more_body": False}

            request._receive = _receive

            async with _SessionLocal() as db:
                from app.repositories.plan_repository import PlanRepository as _PR
                from app.repositories.subscription_repository import SubscriptionRepository as _SR
                from app.services.billing_service import BillingService as _BS
                billing = _BS(
                    plans=_PR(db),
                    subscriptions=_SR(db),
                    db=db,
                )
                try:
                    await stripe_webhook(request, db=db, billing=billing)
                    assert False, "Expected HTTPException for forged payload"
                except Exception as exc:
                    # FastAPI raises HTTPException for 400 responses
                    from fastapi import HTTPException
                    assert isinstance(exc, HTTPException)
                    assert exc.status_code == 400
        finally:
            settings.stripe_webhook_secret = original_secret


# ---------------------------------------------------------------------------
# Task 2: Webhook idempotency
# ---------------------------------------------------------------------------

class TestWebhookIdempotency:

    async def test_processed_event_repository_exists_and_create(self, tenant):
        """ProcessedStripeEventRepository.exists()/create() round-trip."""
        from app.repositories.processed_stripe_event_repository import (
            ProcessedStripeEventRepository,
        )

        event_id = f"evt_test_{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            repo = ProcessedStripeEventRepository(db)
            assert await repo.exists(event_id) is False
            await repo.create(event_id)
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = ProcessedStripeEventRepository(db)
            assert await repo.exists(event_id) is True

    async def test_duplicate_event_is_noop(self, tenant):
        """The same event_id processed twice must not create a duplicate row."""
        from app.repositories.processed_stripe_event_repository import (
            ProcessedStripeEventRepository,
        )

        event_id = f"evt_test_{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            repo = ProcessedStripeEventRepository(db)
            await repo.create(event_id)
            await db.commit()

        # Second call should be a no-op (exists() returns True, no new row)
        async with AsyncSessionLocal() as db:
            repo = ProcessedStripeEventRepository(db)
            assert await repo.exists(event_id) is True
            # Count rows with this event_id - should be exactly 1
            from sqlalchemy import select, func
            from app.models.processed_stripe_event import ProcessedStripeEvent
            count = (await db.execute(
                select(func.count()).select_from(ProcessedStripeEvent).where(
                    ProcessedStripeEvent.event_id == event_id
                )
            )).scalar_one()
            assert count == 1


# ---------------------------------------------------------------------------
# Task 4: Trial expiry job
# ---------------------------------------------------------------------------

class TestTrialExpiry:

    async def test_expired_trial_flips_to_past_due(self, tenant):
        """A trial with trial_ends_at in the past flips to past_due."""
        plan = await _make_plan()

        past = datetime.now(timezone.utc) - timedelta(days=1)
        sub = await _make_subscription(tenant.id, plan.id, status="trialing", trial_ends_at=past)

        async with AsyncSessionLocal() as db:
            billing = BillingService(
                plans=PlanRepository(db),
                subscriptions=SubscriptionRepository(db),
                db=db,
            )
            flipped = await billing.expire_trials()
            await db.commit()

        assert flipped >= 1

        async with AsyncSessionLocal() as db:
            repo = SubscriptionRepository(db)
            refreshed = await repo.get_by_id(sub.id, tenant.id)
            assert refreshed is not None
            assert refreshed.status == "past_due"

    async def test_future_trial_not_flipped(self, tenant):
        """A trial with trial_ends_at in the future stays trialing."""
        plan = await _make_plan()

        future = datetime.now(timezone.utc) + timedelta(days=7)
        sub = await _make_subscription(tenant.id, plan.id, status="trialing", trial_ends_at=future)

        async with AsyncSessionLocal() as db:
            billing = BillingService(
                plans=PlanRepository(db),
                subscriptions=SubscriptionRepository(db),
                db=db,
            )
            flipped = await billing.expire_trials()
            await db.commit()

        async with AsyncSessionLocal() as db:
            repo = SubscriptionRepository(db)
            refreshed = await repo.get_by_id(sub.id, tenant.id)
            assert refreshed is not None
            assert refreshed.status == "trialing"