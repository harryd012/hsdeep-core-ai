"""Pilot safety: per-account login lockout and the public sign-up switch."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.api.routes import auth as auth_routes
from app.core import auth
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.user import User
from app.repositories.permission_repository import PermissionRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.services import auth_service as auth_service_module
from app.services.auth_service import AuthService, InvalidCredentials

PASSWORD = "Correct-Horse-9"


def _service(db) -> AuthService:
    return AuthService(
        users=UserRepository(db),
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
    )


def _limits(monkeypatch, *, attempts: int = 3, minutes: int = 15) -> None:
    fake = SimpleNamespace(login_max_failed_attempts=attempts, login_lockout_minutes=minutes)
    monkeypatch.setattr(auth_service_module, "get_settings", lambda: fake)


async def _user(db, tenant) -> User:
    user = User(
        tenant_id=tenant.id,
        email=f"lockout-{uuid.uuid4().hex[:8]}@hsdeep.local",
        password_hash=auth.hash_password(PASSWORD),
    )
    db.add(user)
    await db.commit()
    return user


async def _login(db, user, password):
    return await _service(db).login(tenant_id=user.tenant_id, email=user.email, password=password)


async def test_account_locks_after_repeated_failures_even_for_the_right_password(tenant, db, monkeypatch):
    _limits(monkeypatch, attempts=3)
    user = await _user(db, tenant)

    for _ in range(3):
        with pytest.raises(InvalidCredentials):
            await _login(db, user, "wrong")
    await db.refresh(user)
    assert user.locked_until is not None and user.locked_until > datetime.now(timezone.utc)

    # Same generic error as a wrong password: the lock is not disclosed.
    with pytest.raises(InvalidCredentials, match="Invalid email or password"):
        await _login(db, user, PASSWORD)


async def test_lock_expires_and_success_resets_the_counter(tenant, db, monkeypatch):
    _limits(monkeypatch, attempts=3)
    user = await _user(db, tenant)
    for _ in range(2):
        with pytest.raises(InvalidCredentials):
            await _login(db, user, "wrong")
    await db.refresh(user)
    assert user.failed_login_count == 2

    await _login(db, user, PASSWORD)
    await db.refresh(user)
    assert (user.failed_login_count, user.locked_until) == (0, None)

    user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    await _login(db, user, PASSWORD)  # an expired lock no longer blocks


async def test_admin_password_reset_unlocks(tenant, db, monkeypatch):
    _limits(monkeypatch, attempts=1)
    user = await _user(db, tenant)
    with pytest.raises(InvalidCredentials):
        await _login(db, user, "wrong")
    await db.refresh(user)
    assert user.locked_until is not None

    await _service(db).set_password(tenant_id=tenant.id, user_id=user.id, password="New-Password-42")
    await db.commit()
    await db.refresh(user)
    assert user.locked_until is None
    await _login(db, user, "New-Password-42")


async def test_zero_attempts_disables_lockout(tenant, db, monkeypatch):
    _limits(monkeypatch, attempts=0)
    user = await _user(db, tenant)
    for _ in range(5):
        with pytest.raises(InvalidCredentials):
            await _login(db, user, "wrong")
    await _login(db, user, PASSWORD)


async def test_login_route_persists_failed_attempts(tenant, db, monkeypatch):
    """The route must commit the counter on a 401, or the lockout never builds up."""
    _limits(monkeypatch, attempts=5)
    monkeypatch.setattr(auth_routes._auth_limiter, "acquire", lambda key: True)
    user = await _user(db, tenant)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # No tenant_id: the normal end-user login (tenant resolved by email).
        resp = await client.post("/api/auth/login", json={"email": user.email, "password": "wrong"})
    assert resp.status_code == 401

    async with AsyncSessionLocal() as fresh:
        stored = await fresh.get(User, user.id)
        assert stored.failed_login_count == 1


async def test_signup_disabled_returns_403(monkeypatch):
    from app.api.routes import billing as billing_routes

    real = billing_routes.get_settings()
    monkeypatch.setattr(
        billing_routes, "get_settings",
        lambda: SimpleNamespace(**{**real.model_dump(), "public_signup_enabled": False}),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/billing/signup", json={
            "org_name": "Pilot Org",
            "email": f"signup-{uuid.uuid4().hex[:6]}@hsdeep.local",
            "password": PASSWORD,
        })
    assert resp.status_code == 403, resp.text
    assert "disabled" in resp.json()["detail"]
