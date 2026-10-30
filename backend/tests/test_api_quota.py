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


class TestControlPlaneExemptFromQuota:
    """Session/control-plane endpoints must survive quota lockout.

    Production evidence for the defect: ``POST /api/auth/login`` returned 200
    (token-in-body, no bearer header -> IP-scoped, never tenant-metered) and
    the immediate follow-up ``GET /api/auth/me`` returned 429
    ``plan_limit_exceeded``, so the tenant could neither establish a session
    nor reach /usage to resolve the lockout once its allowance was spent.

    The exemption must never weaken normal tenant-API enforcement: a normal
    API on the same over-quota tenant must still 429 with an audit trail.
    """

    async def test_auth_me_not_blocked_when_over_quota(self):
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1)
        try:
            user = await _actor_user(tenant.id)
            # Real persisted usage already exceeds the plan allowance.
            await api_quota.record_api_call(tenant.id, 3)
            await api_quota.flush_api_calls(tenant.id)
            api_quota.reset_state()  # force a fresh allowance read

            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer {_jwt(tenant.id, user.id)}"},
                )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["tenant_id"] == str(tenant.id)
            assert body["user_id"] == str(user.id)

            # An allowed control-plane read must never produce a denial audit.
            async with AsyncSessionLocal() as db:
                denials = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.tenant_id == tenant.id,
                                AuditEvent.action == "entitlement.denied",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert denials == []
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_auth_me_is_not_metered_but_normal_api_is(self):
        """The exemption skips the meter; normal tenant APIs still meter."""
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1000)
        try:
            user = await _actor_user(tenant.id)
            headers = {"Authorization": f"Bearer {_jwt(tenant.id, user.id)}"}

            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                me = await client.get("/api/auth/me", headers=headers)
                assert me.status_code == 200, me.text
                # Control-plane read: no api.call increment recorded.
                assert api_quota.pending_for(tenant.id) == 0

                counts = await client.get("/api/tasks/counts", headers=headers)
                assert counts.status_code == 200, counts.text
                # Normal tenant API: metered exactly as before.
                assert api_quota.pending_for(tenant.id) == 1
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_over_quota_normal_api_still_429_and_is_audited(self):
        """The exemption must not weaken enforcement or auditing elsewhere."""
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1)
        try:
            user = await _actor_user(tenant.id)
            await api_quota.record_api_call(tenant.id, 3)
            await api_quota.flush_api_calls(tenant.id)
            api_quota.reset_state()

            headers = {"Authorization": f"Bearer {_jwt(tenant.id, user.id)}"}
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                me = await client.get("/api/auth/me", headers=headers)
                resp = await client.get("/api/tasks/counts", headers=headers)

            # Control-plane survives...
            assert me.status_code == 200, me.text
            # ...while a normal tenant API on the same tenant is still refused.
            assert resp.status_code == 429, resp.text
            assert resp.json()["error"] == "plan_limit_exceeded"
            assert resp.json()["resource"] == "api_calls"

            # The denial for the blocked request is still audited.
            async with AsyncSessionLocal() as db:
                denials = (
                    (
                        await db.execute(
                            select(AuditEvent).where(
                                AuditEvent.tenant_id == tenant.id,
                                AuditEvent.action == "entitlement.denied",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            assert len(denials) == 1
            assert denials[0].context.get("resource") == "api_calls"
            assert denials[0].context.get("error") == "plan_limit_exceeded"
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_cors_preflight_not_blocked_when_over_quota(self):
        """OPTIONS preflight carries no bearer token: IP-scoped, unmetered,
        and never quota-blocked - so CORS handshakes survive lockout too."""
        from app.main import _cors_origins

        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1)
        try:
            await api_quota.record_api_call(tenant.id, 3)
            await api_quota.flush_api_calls(tenant.id)
            api_quota.reset_state()

            origin = _cors_origins[0] if _cors_origins else "http://localhost:3000"
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.options(
                    "/api/tasks/counts",
                    headers={
                        "Origin": origin,
                        "Access-Control-Request-Method": "GET",
                    },
                )

            assert resp.status_code == 200, resp.text
            assert api_quota.pending_for(tenant.id) == 0
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_token_in_body_auth_flow_is_ip_scoped(self):
        """login/refresh/logout carry the token in the BODY (no bearer header),
        so key_for_request scopes them per IP - structurally outside the
        tenant api_calls quota no matter how much the tenant has used."""
        from types import SimpleNamespace

        from app.main import _RateLimiter

        limiter = _RateLimiter()

        class _Req:
            headers: dict = {}
            client = SimpleNamespace(host="203.0.113.9")

        assert limiter.key_for_request(_Req()) == "ip:203.0.113.9"

    async def test_is_control_plane_path_matches_and_normalizes(self):
        """Exact control-plane paths only; trailing slashes normalize; normal
        tenant APIs are never exempt."""
        for path in (
            "/api/auth/login",
            "/api/auth/refresh",
            "/api/auth/logout",
            "/api/auth/me",
        ):
            assert api_quota.is_control_plane_path(path) is True
            assert api_quota.is_control_plane_path(f"{path}/") is True
        for path in ("/api/tasks/counts", "/api/devices", "/api/alerts", "/usage", "/"):
            assert api_quota.is_control_plane_path(path) is False


class TestCorsHeadersOnQuotaResponses:
    """The 429 the rate limiter returns must still carry CORS headers for an
    allowed origin. The limiter used to sit OUTER to CORSMiddleware, so its
    early 429 return bypassed CORS and a browser saw "blocked by CORS" instead
    of the quota error. Regression guard for the middleware reorder."""

    async def test_over_quota_429_includes_allow_origin_for_allowed_origin(self):
        from app.main import _cors_origins

        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1)
        try:
            user = await _actor_user(tenant.id)
            await api_quota.record_api_call(tenant.id, 3)
            await api_quota.flush_api_calls(tenant.id)
            api_quota.reset_state()

            origin = _cors_origins[0] if _cors_origins else "http://localhost:3000"
            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    "/api/tasks/counts",
                    headers={
                        "Authorization": f"Bearer {_jwt(tenant.id, user.id)}",
                        "Origin": origin,
                    },
                )

            assert resp.status_code == 429, resp.text
            assert resp.headers.get("access-control-allow-origin") == origin
            assert resp.headers.get("access-control-allow-credentials") == "true"
        finally:
            await _cleanup(tenant.id, plan_ids)

    async def test_disallowed_origin_gets_no_allow_origin(self):
        tenant, plan_ids = await _seed_tenant_with_plan(api_rate_limit=1)
        try:
            user = await _actor_user(tenant.id)
            await api_quota.record_api_call(tenant.id, 3)
            await api_quota.flush_api_calls(tenant.id)
            api_quota.reset_state()

            async with httpx.AsyncClient(app=app, base_url="http://test") as client:
                resp = await client.get(
                    "/api/tasks/counts",
                    headers={
                        "Authorization": f"Bearer {_jwt(tenant.id, user.id)}",
                        "Origin": "https://evil.example.com",
                    },
                )

            assert resp.status_code == 429, resp.text
            assert "access-control-allow-origin" not in resp.headers
        finally:
            await _cleanup(tenant.id, plan_ids)
