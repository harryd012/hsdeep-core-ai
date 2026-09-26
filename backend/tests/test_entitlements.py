"""
Canonical entitlement tests (Phase 6.4B consolidation).

These test ``app.core.entitlements`` - now the SINGLE source of truth for both
resource-limit and feature decisions. Coverage maps to the required cases:

    A seats enforcement      B collectors enforcement   C no-subscription
    D suspended subscription E enabled feature          F disabled feature
    G unknown feature        H tenant isolation         I denial auditing

A/B/D/I exercise the ACTUAL dependency callables the routes use
(``require_entitlement("seats")`` / ``("collectors")``), invoked directly with
explicit arguments - the same objects FastAPI calls - so no HTTP/auth fixtures
are needed and no real HTTP behaviour is simulated.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.core.entitlements import (
    FeatureNotEntitled,
    ensure_feature,
    evaluate_resource_limit,
    feature_enabled,
    has_feature,
    has_resource_capacity,
    require_entitlement,
    require_feature,
)
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.plan_repository import PlanRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.billing_service import BillingService

pytestmark = pytest.mark.asyncio

#: Real product feature keys, plus deliberately distinctive limits.
FEATURES = {"sla": True, "sso": "limited", "snmp": False, "ai_rca": "disabled"}
DEVICE_LIMIT = 7
SEAT_LIMIT = 5
COLLECTOR_LIMIT = 2


async def _make_tenant(*, active_users: int = 0) -> Tenant:
    async with AsyncSessionLocal() as db:
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant(name="Entitlement Target", slug=f"ent-{suffix}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
        for i in range(active_users):
            db.add(
                User(
                    tenant_id=tenant.id,
                    email=f"ent-{suffix}-{i}@example.com",
                    password_hash="test-hash",
                    full_name=f"Ent User {i}",
                    is_active=True,
                )
            )
        await db.commit()
    return tenant


async def _make_plan(**overrides) -> Plan:
    # Defaults first, then overrides - passing both explicitly would duplicate
    # the keyword (e.g. seats=SEAT_LIMIT plus seats=0).
    fields: dict = {
        "device_limit": DEVICE_LIMIT,
        "seats": SEAT_LIMIT,
        "collector_limit": COLLECTOR_LIMIT,
        "features_json": FEATURES,
    }
    fields.update(overrides)
    async with AsyncSessionLocal() as db:
        plan = Plan(
            name=f"Ent-{uuid.uuid4().hex[:8]}",
            tier=f"ent-{uuid.uuid4().hex[:8]}",
            **fields,
        )
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
    return plan


async def _subscribe(
    tenant_id: uuid.UUID, plan_id: uuid.UUID, *, status: str = "active", blocked: bool = False
) -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        db.add(
            Subscription(
                tenant_id=tenant_id,
                plan_id=plan_id,
                status=status,
                billing_interval="monthly",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
                over_limit_blocked=blocked,
            )
        )
        await db.commit()


def _billing(db) -> BillingService:
    return BillingService(
        plans=PlanRepository(db), subscriptions=SubscriptionRepository(db), db=db
    )


async def _drop(tenant_id: uuid.UUID, plan_id: uuid.UUID | None = None) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM subscriptions WHERE tenant_id = :t"), {"t": tenant_id})
        if plan_id is not None:
            await db.execute(text("DELETE FROM plans WHERE id = :p"), {"p": plan_id})
        await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
        await db.commit()


class TestFeatureEvaluation:
    """E, F, G - pure and programmatic feature checks."""

    async def test_marker_strings_count_as_enabled(self):
        assert feature_enabled({"sla": True}, "sla") is True
        assert feature_enabled({"sso": "limited"}, "sso") is True
        assert feature_enabled({"x": "full"}, "x") is True

    async def test_explicitly_false_or_disabled_denied(self):
        assert feature_enabled({"snmp": False}, "snmp") is False
        assert feature_enabled({"ai_rca": "disabled"}, "ai_rca") is False
        assert feature_enabled({"y": None}, "y") is False
        assert feature_enabled({}, "anything") is False

    async def test_enabled_feature_allowed(self):
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                assert await has_feature(billing, tenant.id, "sla") is True
                assert await has_feature(billing, tenant.id, "sso") is True
        finally:
            await _drop(tenant.id, plan.id)

    async def test_disabled_and_unknown_feature_denied(self):
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                assert await has_feature(billing, tenant.id, "snmp") is False
                assert await has_feature(billing, tenant.id, "ai_rca") is False
                # A phase-example key that does NOT exist must not be granted.
                assert await has_feature(billing, tenant.id, "discovery") is False
                for bad in ("snmp", "ai_rca", "discovery"):
                    with pytest.raises(FeatureNotEntitled):
                        await ensure_feature(billing, tenant.id, bad)
        finally:
            await _drop(tenant.id, plan.id)

    async def test_no_subscription_fails_closed(self):
        """C - no subscription denies every feature."""
        tenant = await _make_tenant()
        try:
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                assert await has_feature(billing, tenant.id, "sla") is False
                with pytest.raises(FeatureNotEntitled):
                    await ensure_feature(billing, tenant.id, "sla")
        finally:
            await _drop(tenant.id)

    async def test_suspended_subscription_fails_closed(self):
        """D - a suspended subscription denies features."""
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id, status="suspended")
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                assert await has_feature(billing, tenant.id, "sla") is False
                denial = await evaluate_resource_limit(billing, tenant.id, "devices")
                assert denial is not None and denial[0] == "no_subscription"
        finally:
            await _drop(tenant.id, plan.id)

    async def test_tenant_isolation(self):
        """H - A's subscription must not entitle B."""
        tenant_a = await _make_tenant()
        tenant_b = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant_a.id, plan.id)
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                assert await has_feature(billing, tenant_a.id, "sla") is True
                assert await has_feature(billing, tenant_b.id, "sla") is False
                assert await has_resource_capacity(billing, tenant_a.id, "devices") is True
                assert await has_resource_capacity(billing, tenant_b.id, "devices") is False
        finally:
            await _drop(tenant_a.id, plan.id)
            await _drop(tenant_b.id)


async def _audits(tenant_id: uuid.UUID, action: str) -> int:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE tenant_id = :t AND action = :a"
                ),
                {"t": tenant_id, "a": action},
            )
        ).scalar_one()


class TestResourceLimitDecision:
    """The canonical limit decision that `require_entitlement` delegates to."""

    async def test_within_limit_allowed(self):
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                # 0 devices against a 7-device plan.
                assert await evaluate_resource_limit(billing, tenant.id, "devices") is None
                assert await has_resource_capacity(billing, tenant.id, "devices") is True
        finally:
            await _drop(tenant.id, plan.id)

    async def test_limit_exceeded_returns_canonical_payload(self):
        """A - seats=0 with one active user is over the seat limit."""
        tenant = await _make_tenant(active_users=1)
        plan = await _make_plan(seats=0)
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                denial = await evaluate_resource_limit(_billing(db), tenant.id, "seats")
                assert denial is not None
                error_code, payload, audit_message = denial
                assert error_code == "plan_limit_exceeded"
                assert payload["error"] == "plan_limit_exceeded"
                assert payload["resource"] == "seats"
                assert payload["limit"] == 0
                assert payload["upgrade_url"] == "/usage"
                assert payload["plan_tier"] == plan.tier
                assert audit_message
        finally:
            await _drop(tenant.id, plan.id)

    async def test_over_limit_blocked_returns_canonical_payload(self):
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id, blocked=True)
            async with AsyncSessionLocal() as db:
                denial = await evaluate_resource_limit(_billing(db), tenant.id, "devices")
                assert denial is not None and denial[0] == "over_limit_blocked"
                assert denial[1]["error"] == "over_limit_blocked"
                assert denial[1]["upgrade_url"] == "/usage"
        finally:
            await _drop(tenant.id, plan.id)

    async def test_unenforced_resource_is_not_speculatively_blocked(self):
        """No canonical plan limit exists for "sites" -> allowed, not invented."""
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                assert await evaluate_resource_limit(_billing(db), tenant.id, "sites") is None
        finally:
            await _drop(tenant.id, plan.id)


class TestHttpDependencies:
    """A, B, C, E, F, G, I - the exact dependency callables the routes use."""

    async def test_seats_enforcement_returns_402_and_audits(self):
        """A + I - require_entitlement("seats") denies over limit and is audited."""
        tenant = await _make_tenant(active_users=1)
        plan = await _make_plan(seats=0)
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                dependency = require_entitlement("seats")
                with pytest.raises(HTTPException) as exc:
                    await dependency(tenant_id=tenant.id, billing=_billing(db))
                assert exc.value.status_code == 402
                assert exc.value.detail["error"] == "plan_limit_exceeded"
                assert exc.value.detail["resource"] == "seats"
                assert exc.value.detail["upgrade_url"] == "/usage"
            # I - the denial was recorded by the canonical audit helper.
            assert await _audits(tenant.id, "entitlement.denied") >= 1
        finally:
            await _drop(tenant.id, plan.id)

    async def test_collectors_enforcement_returns_402(self):
        """B - require_entitlement("collectors") denies over limit."""
        tenant = await _make_tenant()
        plan = await _make_plan(collector_limit=0)
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                denial = await evaluate_resource_limit(billing, tenant.id, "collectors")
                assert denial is not None and denial[1]["resource"] == "collectors"
                dependency = require_entitlement("collectors")
                with pytest.raises(HTTPException) as exc:
                    await dependency(tenant_id=tenant.id, billing=billing)
                assert exc.value.status_code == 402
                assert exc.value.detail["resource"] == "collectors"
        finally:
            await _drop(tenant.id, plan.id)

    async def test_no_subscription_returns_402_and_audits(self):
        """C + I via the HTTP dependency."""
        tenant = await _make_tenant()
        try:
            async with AsyncSessionLocal() as db:
                dependency = require_entitlement("devices")
                with pytest.raises(HTTPException) as exc:
                    await dependency(tenant_id=tenant.id, billing=_billing(db))
                assert exc.value.status_code == 402
                assert exc.value.detail["error"] == "no_subscription"
                assert exc.value.detail["upgrade_url"] == "/usage"
            assert await _audits(tenant.id, "entitlement.denied") >= 1
        finally:
            await _drop(tenant.id)

    async def test_require_feature_denies_disabled_and_unknown(self):
        """F + G via the HTTP dependency."""
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                billing = _billing(db)
                for feature in ("snmp", "discovery"):
                    dependency = require_feature(feature)
                    with pytest.raises(HTTPException) as exc:
                        await dependency(tenant_id=tenant.id, billing=billing)
                    assert exc.value.status_code == 402
                    assert exc.value.detail["error"] == "feature_not_entitled"
                    assert exc.value.detail["feature"] == feature
                    assert exc.value.detail["upgrade_url"] == "/usage"
        finally:
            await _drop(tenant.id, plan.id)

    async def test_require_feature_allows_enabled(self):
        """E via the HTTP dependency."""
        tenant = await _make_tenant()
        plan = await _make_plan()
        try:
            await _subscribe(tenant.id, plan.id)
            async with AsyncSessionLocal() as db:
                dependency = require_feature("sla")
                assert await dependency(tenant_id=tenant.id, billing=_billing(db)) is None
        finally:
            await _drop(tenant.id, plan.id)
