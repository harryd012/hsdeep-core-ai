"""Email foundation: outbox delivery, password reset, verification, invites."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import unquote

import httpx
import pytest
from sqlalchemy import select

from app.api.routes import auth as auth_routes
from app.core import auth
from app.core.auth import create_access_token
from app.core.config import Settings
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.account_token import AccountToken
from app.models.email_outbox import EmailOutbox
from app.models.user import User
from app.repositories.permission_repository import PermissionRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.services.account_service import MAX_RESETS_PER_HOUR, AccountService
from app.services.auth_service import AuthService, EmailNotVerified
from app.services.email import outbox as outbox_module
from app.services.email.outbox import MAX_ATTEMPTS, deliver_due

PASSWORD = "Original-Pass-1"


@pytest.fixture(autouse=True)
def _no_ip_limit(monkeypatch):
    monkeypatch.setattr(auth_routes._auth_limiter, "acquire", lambda key: True)


def _client(headers=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers or {})


def _auth_service(db) -> AuthService:
    return AuthService(
        users=UserRepository(db), roles=RoleRepository(db),
        permissions=PermissionRepository(db), refresh_tokens=RefreshTokenRepository(db),
    )


async def _user(db, tenant, **kw) -> User:
    user = User(
        tenant_id=tenant.id,
        email=f"mail-{uuid.uuid4().hex[:8]}@hsdeep.local",
        password_hash=auth.hash_password(PASSWORD),
        **kw,
    )
    db.add(user)
    await db.commit()
    return user


async def _outbox_for(email: str) -> list[EmailOutbox]:
    async with AsyncSessionLocal() as db:
        return list((await db.execute(
            select(EmailOutbox).where(EmailOutbox.to_address == email).order_by(EmailOutbox.created_at)
        )).scalars())


def _token_in(row: EmailOutbox) -> str:
    match = re.search(r"token=([^\s]+)", row.body_text)
    assert match, row.body_text
    return unquote(match.group(1))


async def _login(email: str, password: str, tenant_id):
    async with AsyncSessionLocal() as db:
        return await _auth_service(db).login(tenant_id=tenant_id, email=email, password=password)


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


async def test_forgot_password_unknown_email_looks_the_same(tenant):
    email = f"nobody-{uuid.uuid4().hex[:6]}@hsdeep.local"
    async with _client() as client:
        resp = await client.post("/api/auth/password/forgot", json={"email": email})
    assert resp.status_code == 202
    assert "If an account exists" in resp.json()["detail"]
    assert await _outbox_for(email) == []


async def test_password_reset_end_to_end_and_single_use(tenant, db):
    user = await _user(db, tenant)
    async with _client() as client:
        assert (await client.post("/api/auth/password/forgot", json={"email": user.email})).status_code == 202
        rows = await _outbox_for(user.email)
        assert [(r.category, r.status) for r in rows] == [("password_reset", "queued")]
        token = _token_in(rows[0])
        assert "http://localhost:3000/reset-password?token=" in rows[0].body_text

        ok = await client.post("/api/auth/password/reset", json={"token": token, "password": "Brand-New-Pass-2"})
        assert ok.status_code == 200, ok.text
        reused = await client.post("/api/auth/password/reset", json={"token": token, "password": "Another-Pass-3"})
        assert reused.status_code == 400

    await _login(user.email, "Brand-New-Pass-2", tenant.id)


async def test_expired_reset_token_is_rejected(tenant, db):
    user = await _user(db, tenant)
    await AccountService(db).request_password_reset(user.email)
    await db.commit()
    token = _token_in((await _outbox_for(user.email))[0])
    async with AsyncSessionLocal() as s:
        row = (await s.execute(select(AccountToken).where(AccountToken.user_id == user.id))).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()
    async with _client() as client:
        resp = await client.post("/api/auth/password/reset", json={"token": token, "password": "Brand-New-Pass-2"})
    assert resp.status_code == 400


async def test_reset_emails_are_capped_per_account(tenant, db):
    user = await _user(db, tenant)
    async with _client() as client:
        for _ in range(MAX_RESETS_PER_HOUR + 2):
            assert (await client.post("/api/auth/password/forgot", json={"email": user.email})).status_code == 202
    assert len(await _outbox_for(user.email)) == MAX_RESETS_PER_HOUR


async def test_email_off_answers_503(tenant, monkeypatch):
    monkeypatch.setattr(outbox_module, "get_settings", lambda: SimpleNamespace(email_transport="none"))
    async with _client() as client:
        resp = await client.post("/api/auth/password/forgot", json={"email": "x@hsdeep.local"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


async def test_unverified_self_signup_cannot_sign_in_until_verified(tenant, db):
    user = await _user(db, tenant, requires_email_verification=True)
    with pytest.raises(EmailNotVerified):
        await _login(user.email, PASSWORD, tenant.id)

    async with _client() as client:
        refused = await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD, "tenant_id": str(tenant.id)})
        assert refused.status_code == 403
        assert "Confirm your email" in refused.json()["detail"]

        await AccountService(db).send_verification(user)
        await db.commit()
        token = _token_in((await _outbox_for(user.email))[-1])
        assert (await client.post("/api/auth/email/verify", json={"token": token})).status_code == 200

    await _login(user.email, PASSWORD, tenant.id)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


async def test_admin_invite_then_accept(tenant, seeded_subscription):
    admin = create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant.id, email="admin@hsdeep.local",
        is_superuser=False, permissions=frozenset({"users.create"}),
    )
    email = f"invitee-{uuid.uuid4().hex[:6]}@hsdeep.local"
    async with _client({"Authorization": f"Bearer {admin}"}) as client:
        created = await client.post("/api/auth/users", json={"email": email, "send_invite": True})
        assert created.status_code == 201, created.text
        missing = await client.post("/api/auth/users", json={"email": f"x{email}"})
        assert missing.status_code == 422  # neither a password nor an invite

    rows = await _outbox_for(email)
    assert [r.category for r in rows] == ["invite"]
    async with _client() as client:
        accepted = await client.post(
            "/api/auth/invite/accept", json={"token": _token_in(rows[0]), "password": "Invitee-Pass-9"}
        )
    assert accepted.status_code == 200, accepted.text
    await _login(email, "Invitee-Pass-9", tenant.id)


# ---------------------------------------------------------------------------
# Outbox delivery
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, message):
        if self.fail:
            raise ConnectionError("smtp down")
        self.sent.append(message.to)


async def _queue(tenant) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        row = await outbox_module.enqueue_email(
            db, to=f"q-{uuid.uuid4().hex[:6]}@hsdeep.local", subject="s", body_text="b",
            category="test", tenant_id=tenant.id,
        )
        await db.commit()
        return row.id


async def test_delivery_sends_and_retries_then_fails(tenant):
    ok_id = await _queue(tenant)
    async with AsyncSessionLocal() as db:
        await deliver_due(db, _Recorder())
        assert (await db.get(EmailOutbox, ok_id)).status == "sent"

    bad_id = await _queue(tenant)
    async with AsyncSessionLocal() as db:
        await deliver_due(db, _Recorder(fail=True))
        row = await db.get(EmailOutbox, bad_id)
        assert (row.status, row.attempts) == ("queued", 1)
        assert "smtp down" in row.last_error
        assert row.next_attempt_at > datetime.now(timezone.utc)

        row.attempts = MAX_ATTEMPTS - 1
        row.next_attempt_at = datetime.now(timezone.utc)
        await db.commit()
        await deliver_due(db, _Recorder(fail=True))
        await db.refresh(row)
        assert row.status == "failed"


def test_production_refuses_the_log_transport():
    cfg = Settings(app_env="production", email_transport="log", secret_encryption_key="x" * 40)
    assert any("EMAIL_TRANSPORT=log" in p for p in cfg.validate_for_production())
