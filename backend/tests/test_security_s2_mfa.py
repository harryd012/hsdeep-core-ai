"""Enterprise security programme S2: multi-factor authentication.

Enrolment (set-up, QR, confirm, recovery codes shown once, secret encrypted),
the two-step sign-in (challenge, TOTP with replay protection, one-time
recovery codes, wrong codes count towards lockout, challenge token is not a
bearer token), the tenant "require MFA" policy (enrolment-only access until
set up), disabling (refused when required) and the admin reset.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.routes import auth as auth_routes
from app.core import auth
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.knowledge_audit import AuditEvent
from app.models.security_setting import SecuritySetting
from app.models.user import User
from app.models.user_mfa import UserMfa
from app.services.mfa import totp

PASSWORD = "Correct-Horse-9-battery"


@pytest.fixture(autouse=True)
def _no_auth_rate_limit(monkeypatch):
    monkeypatch.setattr(auth_routes._auth_limiter, "acquire", lambda key: True)


def _http(token: str | None = None) -> httpx.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)


@pytest_asyncio.fixture
async def people(tenant):
    async with AsyncSessionLocal() as db:
        admin = User(tenant_id=tenant.id, email=f"admin-{uuid.uuid4().hex[:6]}@hsdeep.local",
                     password_hash=auth.hash_password(PASSWORD))
        member = User(tenant_id=tenant.id, email=f"member-{uuid.uuid4().hex[:6]}@hsdeep.local",
                      password_hash=auth.hash_password(PASSWORD))
        db.add_all([admin, member])
        await db.commit()
        return admin, member


async def _login(client, email):
    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


async def _enrol(email) -> tuple[str, list[str], dict]:
    """Sign in, set up and confirm MFA. Returns (secret, recovery codes, tokens)."""
    async with _http() as anon:
        tokens = await _login(anon, email)
    async with _http(tokens["access_token"]) as me:
        setup = (await me.post("/api/auth/mfa/setup")).json()
        assert setup["qr_svg"].startswith("<svg") and setup["otpauth_uri"].startswith("otpauth://totp/")
        bad = await me.post("/api/auth/mfa/confirm", json={"code": "000000"})
        assert bad.status_code == 400
        code = totp.hotp(setup["secret"], totp.current_step())
        ok = await me.post("/api/auth/mfa/confirm", json={"code": code})
        assert ok.status_code == 200, ok.text
        return setup["secret"], ok.json()["codes"], tokens


async def _events(tenant_id, action):
    async with AsyncSessionLocal() as db:
        return list((await db.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.action == action)
        )).scalars())


async def test_enrol_then_two_step_sign_in(tenant, people):
    _, member = people
    secret, codes, tokens = await _enrol(member.email)
    assert len(codes) == 10 and len(set(codes)) == 10

    async with AsyncSessionLocal() as db:
        row = await db.get(UserMfa, member.id)
        assert row.confirmed_at is not None and secret not in row.secret_encrypted

    async with _http(tokens["access_token"]) as me:
        status = (await me.get("/api/auth/mfa")).json()
        assert status["enabled"] is True and status["recovery_codes_remaining"] == 10

    async with _http() as anon:
        challenge = (await anon.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})).json()
        assert challenge["mfa_required"] is True and "access_token" not in challenge
        # The challenge token is not a bearer token.
        async with _http(challenge["mfa_token"]) as sneaky:
            assert (await sneaky.get("/api/auth/me")).status_code == 401

        # The code already used to confirm cannot be used again (replay).
        async with AsyncSessionLocal() as db:
            used_step = (await db.get(UserMfa, member.id)).last_used_step
        same = totp.hotp(secret, used_step)
        replay = await anon.post("/api/auth/login/mfa", json={"mfa_token": challenge["mfa_token"], "code": same})
        assert replay.status_code == 401

        nxt = totp.hotp(secret, totp.current_step() + 1)  # within the drift window
        ok = await anon.post("/api/auth/login/mfa", json={"mfa_token": challenge["mfa_token"], "code": nxt})
        assert ok.status_code == 200 and ok.json()["access_token"]

        # A recovery code works once.
        challenge2 = (await anon.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})).json()
        rc = await anon.post("/api/auth/login/mfa", json={"mfa_token": challenge2["mfa_token"], "code": codes[0].upper()})
        assert rc.status_code == 200
        challenge3 = (await anon.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})).json()
        again = await anon.post("/api/auth/login/mfa", json={"mfa_token": challenge3["mfa_token"], "code": codes[0]})
        assert again.status_code == 401

        bogus = await anon.post("/api/auth/login/mfa", json={"mfa_token": "x" * 40, "code": "123456"})
        assert bogus.status_code == 401

    [used] = await _events(tenant.id, "mfa.recovery_code_used")
    assert used.new_values == {"recovery_codes_remaining": 9}
    assert len(await _events(tenant.id, "mfa.enabled")) == 1


async def test_wrong_codes_count_towards_lockout(tenant, people):
    _, member = people
    await _enrol(member.email)
    async with AsyncSessionLocal() as db:
        db.add(SecuritySetting(tenant_id=tenant.id, min_password_length=8, require_uppercase=False,
                               require_numbers=False, require_special_chars=False, mfa_enabled=False,
                               session_timeout_minutes=480, max_login_attempts=3))
        await db.commit()
    async with _http() as anon:
        challenge = (await anon.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})).json()
        for _ in range(3):
            r = await anon.post("/api/auth/login/mfa", json={"mfa_token": challenge["mfa_token"], "code": "000000"})
            assert r.status_code == 401
        locked = await anon.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})
        assert locked.status_code == 401  # account locked: same generic answer as a wrong password


async def test_require_mfa_policy_limits_unenrolled_users(tenant, people, seeded_subscription):
    _, member = people
    async with AsyncSessionLocal() as db:
        db.add(SecuritySetting(tenant_id=tenant.id, min_password_length=8, require_uppercase=False,
                               require_numbers=False, require_special_chars=False, mfa_enabled=True,
                               session_timeout_minutes=480, max_login_attempts=5))
        await db.commit()

    async with _http() as anon:
        tokens = await _login(anon, member.email)
    async with _http(tokens["access_token"]) as me:
        blocked = await me.get("/api/explorer/hosts")
        assert blocked.status_code == 403 and blocked.json()["detail"].startswith("MFA_ENROLLMENT_REQUIRED")
        assert (await me.get("/api/auth/me")).status_code == 200
        assert (await me.get("/api/settings/security")).status_code == 200
        status = (await me.get("/api/auth/mfa")).json()
        assert status["required_by_policy"] is True and status["enabled"] is False
        setup = (await me.post("/api/auth/mfa/setup")).json()
        code = totp.hotp(setup["secret"], totp.current_step())
        assert (await me.post("/api/auth/mfa/confirm", json={"code": code})).status_code == 200
        # Turning it off is refused while the policy requires it.
        nxt = totp.hotp(setup["secret"], totp.current_step() + 1)
        assert (await me.post("/api/auth/mfa/disable", json={"code": nxt})).status_code == 409

    # The refreshed session no longer carries the enrolment-only restriction.
    async with _http() as anon:
        refreshed = (await anon.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).json()
    async with _http(refreshed["access_token"]) as me:
        assert (await me.get("/api/explorer/hosts")).status_code == 200


async def test_disable_and_admin_reset(tenant, people, seeded_subscription):
    admin, member = people
    secret, _, tokens = await _enrol(member.email)
    async with _http(tokens["access_token"]) as me:
        assert (await me.post("/api/auth/mfa/disable", json={"code": "000000"})).status_code == 400
        code = totp.hotp(secret, totp.current_step() + 1)
        assert (await me.post("/api/auth/mfa/disable", json={"code": code})).status_code == 204
        assert (await me.get("/api/auth/mfa")).json()["enabled"] is False
    assert len(await _events(tenant.id, "mfa.disabled")) == 1

    await _enrol(member.email)
    weak = create_access_token(user_id=admin.id, tenant_id=tenant.id, email=admin.email, is_superuser=False,
                               permissions=frozenset({"users.read"}))
    async with _http(weak) as c:
        assert (await c.post(f"/api/auth/users/{member.id}/mfa/reset")).status_code == 403
    strong = create_access_token(user_id=admin.id, tenant_id=tenant.id, email=admin.email, is_superuser=False,
                                 permissions=frozenset({"security.manage"}))
    async with _http(strong) as c:
        r = await c.post(f"/api/auth/users/{member.id}/mfa/reset")
        assert r.status_code == 200 and r.json()["mfa_removed"] is True
        assert (await c.post(f"/api/auth/users/{uuid.uuid4()}/mfa/reset")).status_code == 404
    async with _http() as anon:
        plain = await anon.post("/api/auth/login", json={"email": member.email, "password": PASSWORD})
        assert "access_token" in plain.json()  # back to password-only until set up again
    [event] = await _events(tenant.id, "mfa.reset_by_admin")
    assert event.entity_id == member.id and event.actor_id == admin.id
