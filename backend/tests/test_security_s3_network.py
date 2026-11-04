"""Enterprise security programme S3: per-tenant IP allowlist, API key scope
validation, and the login rate limit shared across processes.

Allowlist: client address behind trusted proxies (no spoofing), rules API
(security.manage, normalization, lock-out guard, audit), enforcement for
sessions, sign-in (audited), refresh and API keys, other tenants unaffected.
API keys: scopes must exist and be held by the creator. Login limiter: a
Redis outage still enforces the limit per process.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from starlette.requests import Request

from app.api.routes import auth as auth_routes
from app.core import auth
from app.core.auth import create_access_token
from app.core.client_ip import client_ip
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.knowledge_audit import AuditEvent
from app.models.user import User
from app.services import network_policy

PASSWORD = "Correct-Horse-9-battery"
ADMIN = frozenset({"security.manage", "devices.read", "users.read"})
OUTSIDE = ("198.51.100.4", 4321)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(auth_routes._auth_limiter, "limit", 10**9)
    network_policy.forget()
    yield
    network_policy.forget()


def _http(token=None, client=("127.0.0.1", 1234)):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=client), base_url="http://test", headers=headers)


@pytest_asyncio.fixture
async def admin(tenant):
    async with AsyncSessionLocal() as db:
        user = User(tenant_id=tenant.id, email=f"netadmin-{uuid.uuid4().hex[:6]}@hsdeep.local",
                    password_hash=auth.hash_password(PASSWORD))
        db.add(user)
        await db.commit()
        return user


def _token(tenant_id, user_id, perms=ADMIN):
    return create_access_token(user_id=user_id, tenant_id=tenant_id, email="netadmin@hsdeep.local",
                               is_superuser=False, permissions=perms)


def _request(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "client": (peer, 1), "headers": headers, "method": "GET", "path": "/"})


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_client_ip_trusts_only_configured_proxy_hops(monkeypatch):
    from app.core import client_ip as mod

    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(trusted_proxy_hops=0))
    assert client_ip(_request("10.0.0.5", "1.2.3.4")) == "10.0.0.5"  # header ignored without proxies
    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(trusted_proxy_hops=1))
    # The client wrote "6.6.6.6"; the proxy appended the real address last.
    assert client_ip(_request("10.0.0.5", "6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(_request("10.0.0.5", "garbage")) == "10.0.0.5"


def test_cidr_normalization_and_matching():
    assert network_policy.normalize_cidr(" 10.1.2.3/8 ") == "10.0.0.0/8"
    assert network_policy.normalize_cidr("203.0.113.7") == "203.0.113.7/32"
    for bad in ("not-an-ip", "300.1.1.1", "0.0.0.0/0"):
        with pytest.raises(network_policy.InvalidNetwork):
            network_policy.normalize_cidr(bad)
    import ipaddress

    nets = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("2001:db8::/32"))
    assert network_policy.ip_in(nets, "10.9.8.7") and network_policy.ip_in(nets, "::ffff:10.1.1.1")
    assert network_policy.ip_in(nets, "2001:db8::1") and not network_policy.ip_in(nets, "192.0.2.1")
    assert not network_policy.ip_in(nets, None) and network_policy.ip_in((), "192.0.2.1")


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


async def test_ip_allowlist_rules_and_enforcement(tenant, admin, seeded_subscription):
    token = _token(tenant.id, admin.id)
    async with _http(_token(tenant.id, admin.id, frozenset({"devices.read"}))) as viewer:
        assert (await viewer.get("/api/settings/network/ip-rules")).status_code == 403
        assert (await viewer.get("/api/settings/network/my-ip")).json() == {"ip_address": "127.0.0.1"}

    async with _http(token) as c:
        # A first rule that excludes the admin's own address is refused (lock-out guard).
        r = await c.post("/api/settings/network/ip-rules", json={"cidr": "10.0.0.0/8"})
        assert r.status_code == 422 and "127.0.0.1" in r.json()["detail"]
        assert (await c.post("/api/settings/network/ip-rules", json={"cidr": "bogus"})).status_code == 422
        office = (await c.post("/api/settings/network/ip-rules", json={"cidr": "127.0.0.9/8", "label": "Office"})).json()
        assert office["cidr"] == "127.0.0.0/8"
        vpn = (await c.post("/api/settings/network/ip-rules", json={"cidr": "10.0.0.0/8", "label": "VPN"})).json()
        assert (await c.post("/api/settings/network/ip-rules", json={"cidr": "10.0.0.0/8"})).status_code == 409
        assert [x["cidr"] for x in (await c.get("/api/settings/network/ip-rules")).json()] == ["127.0.0.0/8", "10.0.0.0/8"]
        assert (await c.get("/api/explorer/hosts")).status_code == 200

    # From outside every rule: API calls, sign-in and refresh are refused.
    async with _http(token, client=OUTSIDE) as outside:
        r = await outside.get("/api/explorer/hosts")
        assert r.status_code == 403 and r.json()["detail"].startswith("IP_NOT_ALLOWED")
    async with _http(client=OUTSIDE) as anon:
        r = await anon.post("/api/auth/login", json={"email": admin.email, "password": PASSWORD})
        assert r.status_code == 403 and r.json()["detail"].startswith("IP_NOT_ALLOWED")
        wrong = await anon.post("/api/auth/login", json={"email": admin.email, "password": "wrong-password"})
        assert wrong.status_code == 401  # without the password nothing about the allowlist is revealed
    async with _http() as inside:
        tokens = (await inside.post("/api/auth/login", json={"email": admin.email, "password": PASSWORD})).json()
    async with _http(client=OUTSIDE) as anon:
        r = await anon.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 403

    async with AsyncSessionLocal() as db:
        blocked = (await db.execute(select(AuditEvent).where(
            AuditEvent.tenant_id == tenant.id, AuditEvent.action == "auth.login_blocked_network"))).scalars().all()
        assert len(blocked) == 1 and blocked[0].ip_address == OUTSIDE[0]

    async with _http(token) as c:
        # Removing the rule that covers the admin is refused; the other can go.
        assert (await c.delete(f"/api/settings/network/ip-rules/{office['id']}")).status_code == 422
        assert (await c.delete(f"/api/settings/network/ip-rules/{vpn['id']}")).status_code == 204
        assert (await c.delete(f"/api/settings/network/ip-rules/{office['id']}")).status_code == 204  # none left: open
    async with _http(token, client=OUTSIDE) as outside:
        assert (await outside.get("/api/explorer/hosts")).status_code == 200

    async with AsyncSessionLocal() as db:
        actions = sorted(e.action for e in (await db.execute(select(AuditEvent).where(
            AuditEvent.tenant_id == tenant.id, AuditEvent.action.like("network.%")))).scalars())
        assert actions == ["network.ip_rule_added"] * 2 + ["network.ip_rule_removed"] * 2


async def test_allowlist_applies_to_api_keys_and_not_other_tenants(tenant, admin, seeded_subscription):
    token = _token(tenant.id, admin.id)
    async with _http(token) as c:
        key = (await c.post("/api/settings/api-keys", json={"name": "ci", "scopes": ["devices.read"]})).json()["api_key"]
        assert (await c.post("/api/settings/network/ip-rules", json={"cidr": "127.0.0.0/8"})).status_code == 201
    async with _http(key) as inside:
        assert (await inside.get("/api/explorer/hosts")).status_code == 200
    async with _http(key, client=OUTSIDE) as outside:
        assert (await outside.get("/api/explorer/hosts")).status_code == 403

    from app.models.tenant import Tenant

    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        async with _http(_token(other.id, uuid.UUID(int=0), frozenset({"devices.read"})), client=OUTSIDE) as c:
            assert (await c.get("/api/explorer/hosts")).status_code == 200
    finally:
        from sqlalchemy import delete

        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()


# ---------------------------------------------------------------------------
# API key scopes
# ---------------------------------------------------------------------------


async def test_api_key_scopes_must_exist_and_be_held(tenant, admin, seeded_subscription):
    async with _http(_token(tenant.id, admin.id, frozenset({"devices.read"}))) as weak:
        assert (await weak.post("/api/settings/api-keys", json={"name": "x", "scopes": ["devices.read"]})).status_code == 403
        assert (await weak.get("/api/settings/api-keys")).status_code == 403
    async with _http(_token(tenant.id, admin.id)) as c:
        assert (await c.post("/api/settings/api-keys", json={"name": "x", "scopes": []})).status_code == 422
        r = await c.post("/api/settings/api-keys", json={"name": "x", "scopes": ["made.up"]})
        assert r.status_code == 422 and "made.up" in r.json()["detail"]
        r = await c.post("/api/settings/api-keys", json={"name": "x", "scopes": ["devices.read", "devices.write"]})
        assert r.status_code == 403 and "devices.write" in r.json()["detail"]
        ok = await c.post("/api/settings/api-keys", json={"name": "x", "scopes": ["users.read", "devices.read", "devices.read"]})
        assert ok.status_code == 201 and ok.json()["scopes"] == ["devices.read", "users.read"]


# ---------------------------------------------------------------------------
# Shared login rate limit
# ---------------------------------------------------------------------------


class _BrokenRedis:
    def pipeline(self):
        raise ConnectionError("redis down")


async def test_login_limit_holds_during_a_redis_outage(monkeypatch):
    import app.core.redis as redis_mod
    from app.core.shared_rate_limit import SharedRateLimiter

    monkeypatch.setattr(redis_mod, "redis_client", _BrokenRedis())
    limiter = SharedRateLimiter("test:auth:", limit=2, window_seconds=60)
    monkeypatch.setattr(auth_routes, "_auth_limiter", limiter)
    async with _http() as anon:
        codes = [(await anon.post("/api/auth/login", json={"email": "nobody@hsdeep.local", "password": "x"})).status_code
                 for _ in range(3)]
    assert codes == [401, 401, 429]
