"""Break-glass support access (R1.2): a superuser acts in another tenant only
with a live, reasoned, time-bound grant; every grant is audited in the
customer's tenant and the customer can see and revoke it."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core import auth
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.knowledge_audit import AuditEvent
from app.models.support_access_grant import SupportAccessGrant
from app.models.tenant import Tenant
from app.models.user import User

pytestmark = pytest.mark.asyncio
REASON = "Investigating customer ticket 4711"


@pytest_asyncio.fixture
async def customer():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Customer Co", slug=f"cust-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


async def _user(tenant_id, *, superuser: bool) -> User:
    async with AsyncSessionLocal() as db:
        u = User(tenant_id=tenant_id, email=f"u-{uuid.uuid4().hex[:8]}@hsdeep.local", is_superuser=superuser)
        db.add(u)
        await db.commit()
        return u


def _token(user: User, perms=frozenset()) -> str:
    return auth.create_access_token(user_id=user.id, tenant_id=user.tenant_id, email=user.email,
                                    is_superuser=user.is_superuser, permissions=frozenset(perms))


def _client(token: str, target=None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"}
    if target is not None:
        headers["X-Tenant-Id"] = str(target)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)


async def _open(token, customer_id, **kw):
    async with _client(token) as c:
        return await c.post("/api/support-access/grants", json={"tenant_id": str(customer_id), "reason": REASON, **kw})


async def _actions(tenant_id) -> list[str]:
    async with AsyncSessionLocal() as db:
        return list((await db.execute(select(AuditEvent.action).where(AuditEvent.tenant_id == tenant_id))).scalars())


async def test_override_without_a_grant_is_refused(tenant, customer):
    su = await _user(tenant.id, superuser=True)
    async with _client(_token(su), customer.id) as c:
        resp = await c.get("/api/auth/me")
    assert resp.status_code == 403
    assert "break-glass grant" in resp.json()["detail"]


async def test_grant_allows_override_and_is_audited_in_the_customer_tenant(tenant, customer):
    su = await _user(tenant.id, superuser=True)
    created = await _open(_token(su), customer.id, duration_minutes=30)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "active"

    async with _client(_token(su), customer.id) as c:
        me = await c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["tenant_id"] == str(customer.id)
    assert me.json()["home_tenant_id"] == str(tenant.id)
    assert "support_access.granted" in await _actions(customer.id)


@pytest.mark.parametrize("body,detail", [
    ({"reason": "too short"}, "reason"),
    ({"duration_minutes": 100000}, "Duration"),
    ({"duration_minutes": -5}, "Duration"),
])
async def test_grant_needs_a_reason_and_a_bounded_duration(tenant, customer, body, detail):
    su = await _user(tenant.id, superuser=True)
    payload = {"tenant_id": str(customer.id), "reason": REASON, **body}
    async with _client(_token(su)) as c:
        resp = await c.post("/api/support-access/grants", json=payload)
    assert resp.status_code == 422 and detail in resp.text


async def test_only_superusers_open_grants_and_not_for_their_own_tenant(tenant, customer):
    user = await _user(tenant.id, superuser=False)
    assert (await _open(_token(user, {"users.update"}), customer.id)).status_code == 403
    su = await _user(tenant.id, superuser=True)
    assert (await _open(_token(su), tenant.id)).status_code == 422
    assert (await _open(_token(su), uuid.uuid4())).status_code == 422  # unknown tenant


async def test_expired_grant_no_longer_works(tenant, customer):
    su = await _user(tenant.id, superuser=True)
    grant_id = (await _open(_token(su), customer.id)).json()["id"]
    async with AsyncSessionLocal() as db:
        g = await db.get(SupportAccessGrant, uuid.UUID(grant_id))
        g.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()
    async with _client(_token(su), customer.id) as c:
        assert (await c.get("/api/auth/me")).status_code == 403


async def test_customer_admin_sees_and_revokes_grants(tenant, customer):
    su = await _user(tenant.id, superuser=True)
    grant_id = (await _open(_token(su), customer.id)).json()["id"]
    admin = await _user(customer.id, superuser=False)

    async with _client(_token(admin, {"users.read", "users.update"})) as c:
        listed = (await c.get("/api/support-access/grants/tenant")).json()
        assert [(g["id"], g["reason"], g["status"]) for g in listed] == [(grant_id, REASON, "active")]
        revoked = await c.post(f"/api/support-access/grants/{grant_id}/revoke")
    assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"

    async with _client(_token(su), customer.id) as c:
        assert (await c.get("/api/auth/me")).status_code == 403
    assert "support_access.revoked" in await _actions(customer.id)


async def test_other_tenants_cannot_see_or_revoke_a_grant(tenant, customer):
    su = await _user(tenant.id, superuser=True)
    grant_id = (await _open(_token(su), customer.id)).json()["id"]
    outsider = await _user(tenant.id, superuser=False)
    async with _client(_token(outsider, {"users.read", "users.update"})) as c:
        assert (await c.get("/api/support-access/grants/tenant")).json() == []
        assert (await c.post(f"/api/support-access/grants/{grant_id}/revoke")).status_code == 404


async def test_normal_user_override_is_still_forbidden(tenant, customer):
    user = await _user(tenant.id, superuser=False)
    async with _client(_token(user), customer.id) as c:
        assert (await c.get("/api/auth/me")).status_code == 403


async def test_grants_are_not_managed_under_an_override(tenant, customer):
    su = await _user(tenant.id, superuser=True)
    await _open(_token(su), customer.id)
    async with _client(_token(su), customer.id) as c:
        resp = await c.post("/api/support-access/grants", json={"tenant_id": str(tenant.id), "reason": REASON})
    assert resp.status_code == 400
