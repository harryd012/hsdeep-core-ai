"""Scheduler test-package fixtures.

The scheduler now skips sources whose tenant has no ACTIVE subscription
(P1 remediation gap 5). These tests exercise collection itself, so every
test gets an active subscription seeded for the shared `tenant` fixture.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest_asyncio

from app.core.db import AsyncSessionLocal


@pytest_asyncio.fixture(autouse=True)
async def _active_subscription(tenant):
    from app.models.plan import Plan
    from app.models.subscription import Subscription

    async with AsyncSessionLocal() as db:
        plan = Plan(
            name=f"Sched-{uuid.uuid4().hex[:8]}",
            tier=f"sched-{uuid.uuid4().hex[:8]}",
            description="scheduler test plan",
            price_monthly=0,
            price_annual=0,
            device_limit=1000,
            sensor_limit=10000,
            api_rate_limit=100000,
            collector_limit=100,
            seats=25,
            features_json={},
            is_public=False,
        )
        db.add(plan)
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add(Subscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            billing_interval="month",
        ))
        await db.commit()
    yield
    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete

        from app.models.tenant import Tenant

        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()
