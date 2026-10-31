"""Server-side session revocation (R1.4): access tokens stop working when
their sign-in session ends, not only when they expire."""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import update

from app.api.routes import auth as auth_routes
from app.core import auth
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.user import User

pytestmark = pytest.mark.asyncio
PASSWORD = "Original-Pass-1"


@pytest.fixture(autouse=True)
def _no_cache_no_ip_limit(monkeypatch):
    monkeypatch.setattr(auth_routes._auth_limiter, "acquire", lambda key: True)
    monkeypatch.setattr(auth.settings, "session_check_cache_seconds", 0)
    auth.forget_session()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _user(tenant) -> User:
    async with AsyncSessionLocal() as db:
        user = User(tenant_id=tenant.id, email=f"s-{uuid.uuid4().hex[:8]}@hsdeep.local",
                    password_hash=auth.hash_password(PASSWORD))
        db.add(user)
        await db.commit()
        return user


async def _login(client, user, tenant) -> dict:
    resp = await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD, "tenant_id": str(tenant.id)})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _me(client, access: str) -> int:
    return (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})).status_code


async def test_logout_ends_the_access_token_immediately(tenant):
    user = await _user(tenant)
    async with _client() as client:
        tokens = await _login(client, user, tenant)
        assert await _me(client, tokens["access_token"]) == 200
        assert (await client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})).status_code == 204
        assert await _me(client, tokens["access_token"]) == 401


async def test_refresh_keeps_the_session_and_replay_ends_it(tenant):
    user = await _user(tenant)
    async with _client() as client:
        first = await _login(client, user, tenant)
        second = (await client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})).json()
        # Rotation keeps the session: the earlier access token still works.
        assert await _me(client, first["access_token"]) == 200
        assert await _me(client, second["access_token"]) == 200

        replay = await client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
        assert replay.status_code == 401 and "already used" in replay.json()["detail"]
        # The whole session ended: both access tokens and the newest refresh token.
        assert await _me(client, second["access_token"]) == 401
        assert (await client.post("/api/auth/refresh", json={"refresh_token": second["refresh_token"]})).status_code == 401


async def test_sessions_are_independent_and_logout_all_ends_every_one(tenant):
    user = await _user(tenant)
    async with _client() as client:
        laptop, phone = await _login(client, user, tenant), await _login(client, user, tenant)
        await client.post("/api/auth/logout", json={"refresh_token": laptop["refresh_token"]})
        assert await _me(client, laptop["access_token"]) == 401
        assert await _me(client, phone["access_token"]) == 200

        other = await _login(client, user, tenant)
        resp = await client.post("/api/auth/logout-all", headers={"Authorization": f"Bearer {other['access_token']}"})
        assert resp.status_code == 204
        assert await _me(client, phone["access_token"]) == 401
        assert await _me(client, other["access_token"]) == 401


async def test_disabling_the_user_ends_their_sessions(tenant):
    user = await _user(tenant)
    async with _client() as client:
        tokens = await _login(client, user, tenant)
        async with AsyncSessionLocal() as db:
            await db.execute(update(User).where(User.id == user.id).values(is_active=False))
            await db.commit()
        assert await _me(client, tokens["access_token"]) == 401


async def test_password_change_ends_sessions(tenant):
    from app.repositories.permission_repository import PermissionRepository
    from app.repositories.refresh_token_repository import RefreshTokenRepository
    from app.repositories.role_repository import RoleRepository
    from app.repositories.user_repository import UserRepository
    from app.services.auth_service import AuthService

    user = await _user(tenant)
    async with _client() as client:
        tokens = await _login(client, user, tenant)
        async with AsyncSessionLocal() as db:
            svc = AuthService(users=UserRepository(db), roles=RoleRepository(db),
                              permissions=PermissionRepository(db), refresh_tokens=RefreshTokenRepository(db))
            await svc.set_password(tenant_id=tenant.id, user_id=user.id, password="Brand-New-Pass-2")
            await db.commit()
        assert await _me(client, tokens["access_token"]) == 401


async def test_cache_keeps_a_live_session_cheap_and_local_logout_clears_it(tenant, monkeypatch):
    monkeypatch.setattr(auth.settings, "session_check_cache_seconds", 60)
    user = await _user(tenant)
    async with _client() as client:
        tokens = await _login(client, user, tenant)
        assert await _me(client, tokens["access_token"]) == 200
        assert auth._live_sessions  # cached
        await client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        assert await _me(client, tokens["access_token"]) == 401


async def test_tokens_without_a_session_claim_still_work(tenant):
    """Tokens minted before sessions existed (or by tooling) carry no sid and
    keep working until they expire."""
    user = await _user(tenant)
    token = auth.create_access_token(user_id=user.id, tenant_id=tenant.id, email=user.email,
                                     is_superuser=False, permissions=frozenset())
    async with _client() as client:
        assert await _me(client, token) == 200


async def test_unknown_session_is_rejected(tenant):
    user = await _user(tenant)
    token = auth.create_access_token(user_id=user.id, tenant_id=tenant.id, email=user.email,
                                     is_superuser=False, permissions=frozenset(), session_id=uuid.uuid4())
    async with _client() as client:
        assert await _me(client, token) == 401
