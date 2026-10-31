"""scripts/seed_admin.py can bootstrap a FRESH deployment: tenant, plan
catalog, active subscription, Owner user - with no hand-written SQL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from app.core.db import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from scripts.seed_admin import _slugify, seed_admin

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fresh_tenant_env(monkeypatch):
    tenant_id = uuid.uuid4()
    monkeypatch.setenv("SEED_TENANT_ID", str(tenant_id))
    monkeypatch.setenv("SEED_TENANT_NAME", "Bootstrap Test MSP")
    monkeypatch.setenv("SEED_TENANT_SLUG", f"bootstrap-{tenant_id.hex[:8]}")
    monkeypatch.setenv("SEED_ADMIN_EMAIL", f"owner-{tenant_id.hex[:8]}@test.local")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", f"Test-{uuid.uuid4().hex}")
    monkeypatch.setenv("SEED_PLAN_TIER", "enterprise")
    yield tenant_id


async def _cleanup(tenant_id):
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()


async def test_fresh_database_bootstrap(fresh_tenant_env):
    tenant_id = fresh_tenant_env
    try:
        async with AsyncSessionLocal() as db:
            assert await seed_admin(db) == 0
            await db.commit()

        async with AsyncSessionLocal() as db:
            tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
            assert tenant.slug.startswith("bootstrap-")
            user = (await db.execute(select(User).where(User.tenant_id == tenant_id))).scalar_one()
            assert user.is_superuser is True
            sub = await SubscriptionRepository(db).get_active(tenant_id)
            assert sub is not None and sub.status == "active"
            for tier in ("starter", "pro", "enterprise"):
                assert await PlanRepository(db).get_by_tier(tier) is not None

        # Idempotent: a second run changes nothing and still succeeds.
        async with AsyncSessionLocal() as db:
            assert await seed_admin(db) == 0
            await db.commit()
        async with AsyncSessionLocal() as db:
            users = (await db.execute(select(User).where(User.tenant_id == tenant_id))).scalars().all()
            assert len(users) == 1
    finally:
        await _cleanup(tenant_id)


async def test_missing_tenant_without_name_aborts(monkeypatch):
    monkeypatch.setenv("SEED_TENANT_ID", str(uuid.uuid4()))
    monkeypatch.setenv("SEED_ADMIN_EMAIL", "nobody@test.local")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "x")
    monkeypatch.delenv("SEED_TENANT_NAME", raising=False)
    monkeypatch.delenv("SEED_PLAN_TIER", raising=False)
    async with AsyncSessionLocal() as db:
        assert await seed_admin(db) == 1
        await db.rollback()


def test_slugify():
    assert _slugify("Acme Networks, Inc.") == "acme-networks-inc"
    assert _slugify("!!!") == "tenant"
