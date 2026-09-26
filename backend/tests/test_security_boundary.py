"""
Focused security-boundary tests (SaaS Foundation sprint).

Covers the sprint's DoD items not already proven by test_tenant_isolation.py:
  1. A revoked API key cannot authenticate (401).
  2. A principal without an admin permission cannot perform an admin-only
     operation (403) — RBAC enforced server-side.
  3. A usage limit actually blocks once quota is exceeded (BillingService).
  4. Security-sensitive operations (API key create/revoke) produce audit
     events in the existing audit_events table.

Runs against the real Postgres DB per project convention (see conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.auth import create_access_token, hash_api_key
from app.core.db import AsyncSessionLocal
from app.main import app

from app.models.api_key import ApiKey
from app.models.knowledge_audit import AuditEvent
from app.models.plan import Plan
from app.models.subscription import Subscription
from app.models.usage import UsageCounter


_ADMIN_PERMS = frozenset({
    "users.create", "users.read", "users.update", "users.delete",
    "roles.read", "roles.write", "settings.manage", "monitoring.read",
    "monitoring.create", "billing.read", "dashboard.read",
})


async def _make_admin_user(
    tenant_id: uuid.UUID, *, is_superuser: bool = True
) -> tuple:
    """Create a REAL committed user row (audit_events.actor_id has an FK to
    users.id, so audit-attributed actions need a real actor) and return
    (user, jwt). Superuser by default because /settings/* writes are guarded
    by require_admin (superuser-only), matching production behaviour."""
    from app.core.auth import hash_password
    from app.models.user import User

    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"
    async with AsyncSessionLocal() as db:
        user = User(
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password("Str0ngPass!x"),
            full_name="Test Admin",
            is_active=True,
            is_superuser=is_superuser,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    token = create_access_token(
        user_id=user.id,
        tenant_id=tenant_id,
        email=email,
        is_superuser=is_superuser,
        permissions=_ADMIN_PERMS,
    )
    return user, token


def _admin_jwt(tenant_id: uuid.UUID) -> str:
    """Synthetic-actor token. Fine for endpoints that don't write audit rows;
    use _make_admin_user when the action is audited."""
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="admin@example.com",
        is_superuser=False,
        permissions=_ADMIN_PERMS,
    )


def _viewer_jwt(tenant_id: uuid.UUID) -> str:
    """A Viewer-scoped principal: read-only permissions only."""
    return create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="viewer@example.com",
        is_superuser=False,
        permissions=frozenset({"alerts.read", "dashboard.read", "devices.read"}),
    )


class TestRevokedApiKeyCannotAuthenticate:
    async def test_active_key_authenticates_then_revoked_key_401s(self, tenant, db):
        raw = "hsdeep-test-key-" + uuid.uuid4().hex
        key = ApiKey(
            tenant_id=tenant.id,
            name="revocation-test",
            key_prefix=raw[:12],
            key_hash=hash_api_key(raw),
            scopes=["dashboard.read"],
            is_active=True,
        )
        db.add(key)
        await db.commit()

        headers = {"Authorization": f"Bearer {raw}"}
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            ok = await client.get("/api/auth/me", headers=headers)
            assert ok.status_code == 200, ok.text

            # Revoke (same semantics as DELETE /api/settings/api-keys/{id}).
            key.is_active = False
            await db.commit()

            revoked = await client.get("/api/auth/me", headers=headers)
            assert revoked.status_code == 401, (
                f"Revoked API key must not authenticate, got {revoked.status_code}"
            )


class TestRbacDeny:
    @pytest.mark.usefixtures("seeded_subscription")
    async def test_viewer_cannot_create_user(self, tenant):
        """A Viewer-scoped principal gets 403 on the admin-only user-create
        endpoint — permission checks are enforced server-side."""
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/users",
                headers={"Authorization": f"Bearer {_viewer_jwt(tenant.id)}"},
                json={
                    "email": "nope@example.com",
                    "password": "Str0ngPass!x",
                    "full_name": "Should Not Exist",
                    "is_superuser": False,
                    "role_ids": [],
                },
            )
        assert resp.status_code == 403, (
            f"Viewer must be denied user creation, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.usefixtures("seeded_subscription")
    async def test_admin_can_create_user(self, tenant, db):
        """Sanity: the same endpoint succeeds for a principal holding
        users.create (proves the 403 above is RBAC, not a broken route)."""
        from app.models.user import User
        admin, token = await _make_admin_user(tenant.id)
        email = f"rbac-ok-{uuid.uuid4().hex[:8]}@example.com"
        try:
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.post(
                    "/api/auth/users",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "email": email,
                        "password": "Str0ngPass!x",
                        "full_name": "RBAC Sanity",
                        "is_superuser": False,
                        "role_ids": [],
                    },
                )
            assert resp.status_code == 201, resp.text
        finally:
            async with AsyncSessionLocal() as cleanup:
                await cleanup.execute(delete(User).where(User.tenant_id == tenant.id))
                await cleanup.commit()


class TestUsageLimitEnforcement:
    async def test_exceeded_api_call_quota_blocks(self, tenant, db):
        """Once the tenant's api.call usage for the period exceeds the plan's
        api_rate_limit, BillingService.check_limit denies further use."""
        from app.repositories.plan_repository import PlanRepository
        from app.repositories.subscription_repository import SubscriptionRepository
        from app.services.billing_service import BillingService

        plan = await PlanRepository(db).create(
            name="LimitTest",
            tier=f"limit-test-{uuid.uuid4().hex[:8]}",
            description="test",
            price_monthly=0.0,
            price_annual=0.0,
            device_limit=10,
            sensor_limit=100,
            api_rate_limit=5,
            collector_limit=1,
            seats=1,
            features_json={},
            is_public=False,
        )
        now = datetime.now(timezone.utc)
        # The billing period must start at/below the usage bucket so the
        # period-sum in BillingService._sum_usage includes the seeded count.
        period_start = now.replace(minute=0, second=0, microsecond=0)
        sub = await SubscriptionRepository(db).create(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status="active",
            current_period_start=period_start,
            current_period_end=now,
            billing_interval="month",
        )
        # Seed usage ABOVE the plan's api_rate_limit (5).
        db.add(UsageCounter(
            tenant_id=tenant.id,
            event_type="api.call",
            bucket_start=period_start,
            count=100,
        ))
        await db.commit()

        try:
            billing = BillingService(
                plans=PlanRepository(db),
                subscriptions=SubscriptionRepository(db),
                db=db,
            )
            allowed, info = await billing.check_limit(tenant.id, "api_calls")
            assert allowed is False, "Quota exceeded but check_limit allowed the operation"
            assert info is not None and info["limit"] == 5 and info["used"] >= 5
        finally:
            await db.execute(delete(UsageCounter).where(UsageCounter.tenant_id == tenant.id))
            await db.execute(delete(Subscription).where(Subscription.id == sub.id))
            await db.execute(delete(Plan).where(Plan.id == plan.id))
            await db.commit()


class TestAuditEvents:
    @pytest.mark.usefixtures("seeded_subscription")
    async def test_api_key_create_and_revoke_produce_audit_events(self, tenant):
        """Creating and revoking an API key via the settings API writes
        api_key.created / api_key.revoked rows to audit_events."""
        _, admin_token = await _make_admin_user(tenant.id)
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            created = await client.post(
                "/api/settings/api-keys",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"name": "audit-test-key", "scopes": ["dashboard.read"]},
            )
            assert created.status_code == 201, created.text
            key_id = created.json()["id"]
            raw_key = created.json()["api_key"]

            # The raw secret must appear exactly once (in the creation
            # response) and never in any audit event payload.
            revoked = await client.delete(
                f"/api/settings/api-keys/{key_id}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert revoked.status_code == 204, revoked.text

        async with AsyncSessionLocal() as check:
            rows = (
                await check.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant.id,
                        AuditEvent.entity_type == "api_key",
                    )
                )
            ).scalars().all()
            actions = {r.action for r in rows}
            assert "api_key.created" in actions, f"Missing api_key.created audit event, got {actions}"
            assert "api_key.revoked" in actions, f"Missing api_key.revoked audit event, got {actions}"
            # Secret-hygiene: the raw key material must never be audited.
            for r in rows:
                blob = str(r.new_values) + str(r.old_values) + str(r.description) + str(r.context)
                assert raw_key not in blob, "Raw API key leaked into audit event!"

        # The revoked key must no longer authenticate.
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.get(
                "/api/auth/me", headers={"Authorization": f"Bearer {raw_key}"}
            )
        assert resp.status_code == 401

    async def test_login_failure_produces_audit_event(self, tenant):
        async with httpx.AsyncClient(app=app, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login",
                json={
                    "tenant_id": str(tenant.id),
                    "email": "ghost@example.com",
                    "password": "wrong",
                },
            )
        assert resp.status_code == 401

        async with AsyncSessionLocal() as check:
            row = (
                await check.execute(
                    select(AuditEvent).where(
                        AuditEvent.tenant_id == tenant.id,
                        AuditEvent.action == "auth.login_failed",
                    )
                )
            ).scalar_one_or_none()
            assert row is not None, "Failed login did not produce an audit event"