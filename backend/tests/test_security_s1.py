"""Enterprise security programme S1: the tenant security policy is enforced,
sessions can be listed and ended, and the audit log can be read and exported.

Policy: password rules wherever a password is set, the saved lockout threshold,
the idle timeout at refresh. Settings: security.manage, validation, MFA not
switchable yet, every change audited. Sessions: list (current marked), end one,
end the others, an admin signing a user out. Audit log: audit.read (not
Viewer), filters, tenant isolation, CSV export with formula escaping, the
export itself audited; role and user changes are audited.
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from app.api.routes import auth as auth_routes
from app.core import auth
from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.core.permissions import DEFAULT_ROLES, resolve_role_permissions
from app.main import app
from app.models.knowledge_audit import AuditEvent
from app.models.refresh_token import RefreshToken
from app.models.security_setting import SecuritySetting
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.permission_repository import PermissionRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService, InvalidCredentials, SessionIdleTimeout
from app.services.security_policy import PasswordPolicyError, SecurityPolicy

PASSWORD = "Correct-Horse-9-battery"
ADMIN = frozenset({"security.manage", "audit.read", "users.read", "users.update", "roles.read", "roles.write"})


def _service(db) -> AuthService:
    return AuthService(
        users=UserRepository(db), roles=RoleRepository(db),
        permissions=PermissionRepository(db), refresh_tokens=RefreshTokenRepository(db),
    )


def _client(tenant_id, permissions, user_id, token: str | None = None):
    token = token or create_access_token(
        user_id=user_id, tenant_id=tenant_id, email="admin@hsdeep.local", is_superuser=False, permissions=permissions,
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": f"Bearer {token}"})


@pytest.fixture(autouse=True)
def _no_auth_rate_limit(monkeypatch):
    monkeypatch.setattr(auth_routes._auth_limiter, "acquire", lambda key: True)


@pytest_asyncio.fixture
async def people(tenant):
    """An admin and a member with known passwords (audit rows reference users.id)."""
    async with AsyncSessionLocal() as db:
        admin = User(tenant_id=tenant.id, email=f"admin-{uuid.uuid4().hex[:6]}@hsdeep.local",
                     password_hash=auth.hash_password(PASSWORD))
        member = User(tenant_id=tenant.id, email=f"member-{uuid.uuid4().hex[:6]}@hsdeep.local",
                      password_hash=auth.hash_password(PASSWORD))
        db.add_all([admin, member])
        await db.commit()
        return admin, member


async def _policy(tenant_id, **fields):
    async with AsyncSessionLocal() as db:
        db.add(SecuritySetting(tenant_id=tenant_id, **fields))
        await db.commit()


async def _events(tenant_id, prefix):
    async with AsyncSessionLocal() as db:
        return list((await db.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id, AuditEvent.action.like(f"{prefix}%"))
            .order_by(AuditEvent.created_at)
        )).scalars())


# ---------------------------------------------------------------------------
# Policy (pure)
# ---------------------------------------------------------------------------


def test_password_rules_defaults_and_strict():
    default = SecurityPolicy.defaults()
    assert default.password_problems("password123") == []  # NIST-style default: length only
    assert default.password_problems("short") == ["at least 8 characters"]
    assert "not your email name" in default.password_problems("jane.doe-2026!", "jane.doe@corp.test")

    strict = SecurityPolicy(14, True, True, True, 60, 5, saved=True)
    assert strict.password_problems("alllowercase") == [
        "at least 14 characters", "an upper-case letter", "a number", "a symbol",
    ]
    with pytest.raises(PasswordPolicyError, match="The password needs an upper-case letter"):
        strict.check_password("lowercase-only-1234")
    strict.check_password("Upper-and-1234-ok")


def test_viewer_does_not_get_audit_read():
    viewer = resolve_role_permissions(DEFAULT_ROLES["Viewer"][1])
    assert "audit.read" not in viewer and "security.manage" not in viewer
    admin = resolve_role_permissions(DEFAULT_ROLES["Admin"][1])
    assert {"audit.read", "security.manage"} <= admin


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


async def test_saved_policy_rules_apply_where_passwords_are_set(tenant, people):
    admin, member = people
    await _policy(tenant.id, min_password_length=12, require_uppercase=True, require_numbers=True,
                  require_special_chars=False, session_timeout_minutes=60, max_login_attempts=5)
    async with AsyncSessionLocal() as db:
        with pytest.raises(PasswordPolicyError):
            await _service(db).set_password(tenant_id=tenant.id, user_id=member.id, password="nouppercase123")
        with pytest.raises(PasswordPolicyError):
            await _service(db).create_user(tenant_id=tenant.id, email="x@corp.test", password="Short1",
                                           full_name=None, is_superuser=False, role_ids=[])
    async with _client(tenant.id, ADMIN, admin.id) as c:
        r = await c.post(f"/api/auth/users/{member.id}/password", json={"password": "alllowercase-123"})
        assert r.status_code == 422 and "upper-case" in r.json()["detail"]
        ok = await c.post(f"/api/auth/users/{member.id}/password", json={"password": "Strong-Enough-2026"})
        assert ok.status_code == 200, ok.text
    assert [e.action for e in await _events(tenant.id, "user.password_set")] == ["user.password_set"]


async def test_saved_lockout_threshold_applies(tenant, people):
    _, member = people
    await _policy(tenant.id, min_password_length=8, require_uppercase=False, require_numbers=False,
                  require_special_chars=False, session_timeout_minutes=60, max_login_attempts=3)
    async with AsyncSessionLocal() as db:
        for _ in range(3):
            with pytest.raises(InvalidCredentials):
                await _service(db).login(tenant_id=tenant.id, email=member.email, password="wrong")
        await db.commit()
        stored = await db.get(User, member.id)
        await db.refresh(stored)
        assert stored.locked_until is not None


async def test_idle_timeout_ends_the_session_at_refresh(tenant, people):
    _, member = people
    await _policy(tenant.id, min_password_length=8, require_uppercase=False, require_numbers=False,
                  require_special_chars=False, session_timeout_minutes=30, max_login_attempts=5)
    async with AsyncSessionLocal() as db:
        _, refresh, _, _ = await _service(db).login(tenant_id=tenant.id, email=member.email, password=PASSWORD)
        await db.commit()
        # Used 20 minutes ago: still within 30 minutes, so it refreshes.
        await db.execute(update(RefreshToken).where(RefreshToken.user_id == member.id)
                         .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=20)))
        await db.commit()
        _, refresh2, _ = await _service(db).refresh(refresh_token=refresh)
        await db.commit()
        # Idle for 31 minutes: the refresh fails and the whole session ends.
        await db.execute(update(RefreshToken).where(RefreshToken.user_id == member.id, RefreshToken.revoked_at.is_(None))
                         .values(created_at=datetime.now(timezone.utc) - timedelta(minutes=31)))
        await db.commit()
        with pytest.raises(SessionIdleTimeout, match="30 minutes"):
            await _service(db).refresh(refresh_token=refresh2)
        await db.commit()
        live = (await db.execute(select(RefreshToken).where(RefreshToken.user_id == member.id,
                                                            RefreshToken.revoked_at.is_(None)))).scalars().all()
        assert live == []


# ---------------------------------------------------------------------------
# Security settings API
# ---------------------------------------------------------------------------


async def test_security_settings_permission_validation_and_audit(tenant, people, seeded_subscription):
    admin, member = people
    async with _client(tenant.id, frozenset({"devices.read"}), member.id) as viewer:
        assert (await viewer.put("/api/settings/security", json={"max_login_attempts": 4})).status_code == 403
    async with _client(tenant.id, ADMIN, admin.id) as c:
        current = (await c.get("/api/settings/security")).json()
        assert current["is_default"] is True and current["require_uppercase"] is False
        assert current["session_timeout_minutes"] == 480
        for bad in ({"session_timeout_minutes": 5}, {"max_login_attempts": 1}, {"min_password_length": 6},
                    {"mfa_enabled": True}):
            assert (await c.put("/api/settings/security", json=bad)).status_code == 422, bad
        r = await c.put("/api/settings/security", json={"min_password_length": 12, "require_numbers": True})
        assert r.status_code == 200, r.text
        assert r.json()["min_password_length"] == 12 and r.json()["session_timeout_minutes"] == 480
    [event] = await _events(tenant.id, "security.policy_updated")
    assert event.old_values == {"min_password_length": 8, "require_numbers": False}
    assert event.new_values == {"min_password_length": 12, "require_numbers": True}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def _sign_in(client: httpx.AsyncClient, email: str, agent: str) -> dict:
    r = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers={"User-Agent": agent})
    assert r.status_code == 200, r.text
    return r.json()


async def test_sessions_list_end_one_and_end_others(tenant, people):
    admin, member = people
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        laptop = await _sign_in(anon, member.email, "Laptop-Browser")
        phone = await _sign_in(anon, member.email, "Phone-Browser")
        tablet = await _sign_in(anon, member.email, "Tablet-Browser")

    async with _client(tenant.id, None, None, token=laptop["access_token"]) as me:
        sessions = (await me.get("/api/auth/sessions")).json()
        assert len(sessions) == 3
        assert [s["user_agent"] for s in sessions if s["current"]] == ["Laptop-Browser"]
        phone_sid = next(s["session_id"] for s in sessions if s["user_agent"] == "Phone-Browser")
        assert (await me.delete(f"/api/auth/sessions/{phone_sid}")).status_code == 204
        assert (await me.delete(f"/api/auth/sessions/{phone_sid}")).status_code == 404
        assert (await me.post("/api/auth/sessions/end-others")).json() == {"sessions_ended": 1}
        left = (await me.get("/api/auth/sessions")).json()
        assert [s["user_agent"] for s in left] == ["Laptop-Browser"]

    async with _client(tenant.id, None, None, token=tablet["access_token"]) as ended:
        assert (await ended.get("/api/auth/sessions")).status_code == 401
    async with _client(tenant.id, None, None, token=phone["access_token"]) as ended:
        assert (await ended.get("/api/auth/me")).status_code == 401

    # Another user cannot end this user's session.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        admin_login = await _sign_in(anon, admin.email, "Admin-Browser")
    async with _client(tenant.id, None, None, token=admin_login["access_token"]) as other:
        laptop_sid = left[0]["session_id"]
        assert (await other.delete(f"/api/auth/sessions/{laptop_sid}")).status_code == 404

    actions = sorted(e.action for e in await _events(tenant.id, "auth."))
    assert "auth.session_ended" in actions and "auth.other_sessions_ended" in actions


async def test_admin_signs_a_user_out_everywhere(tenant, people, seeded_subscription):
    admin, member = people
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anon:
        session = await _sign_in(anon, member.email, "Lost-Laptop")
    async with _client(tenant.id, frozenset({"users.read"}), admin.id) as weak:
        assert (await weak.post(f"/api/auth/users/{member.id}/sessions/end")).status_code == 403
    async with _client(tenant.id, ADMIN, admin.id) as c:
        r = await c.post(f"/api/auth/users/{member.id}/sessions/end")
        assert r.status_code == 200 and r.json()["sessions_ended"] >= 1
        assert (await c.post(f"/api/auth/users/{uuid.uuid4()}/sessions/end")).status_code == 404
    async with _client(tenant.id, None, None, token=session["access_token"]) as ended:
        assert (await ended.get("/api/auth/me")).status_code == 401
    [event] = await _events(tenant.id, "auth.user_signed_out")
    assert event.entity_id == member.id and event.actor_id == admin.id


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_audit_log_filters_isolation_and_export(tenant, people, seeded_subscription):
    admin, member = people
    async with _client(tenant.id, ADMIN, admin.id) as c:
        r = await c.post("/api/auth/roles", json={"name": "Auditors", "permission_codes": ["audit.read"]})
        assert r.status_code == 201, r.text
        role_id = r.json()["id"]
        assert (await c.patch(f"/api/auth/roles/{role_id}", json={"permission_codes": ["audit.read", "devices.read"]})).status_code == 200
        assert (await c.post(f"/api/auth/users/{member.id}/roles", json={"role_ids": [role_id]})).status_code == 200

    async with AsyncSessionLocal() as db:
        db.add(AuditEvent(tenant_id=tenant.id, actor_id=admin.id, actor_email="=HYPERLINK(\"http://evil\")",
                          action="settings.updated", entity_type="settings", description="crafted"))
        await db.commit()

    async with _client(tenant.id, ADMIN, admin.id) as c:
        page = (await c.get("/api/audit/events", params={"action": "role."})).json()
        assert [r["action"] for r in page["rows"]] == ["role.updated", "role.created"]
        updated = page["rows"][0]
        assert updated["old_values"]["permission_codes"] == ["audit.read"]
        assert updated["new_values"]["permission_codes"] == ["audit.read", "devices.read"]
        roles_changed = (await c.get("/api/audit/events", params={"action": "user.roles_changed"})).json()
        assert roles_changed["total"] == 1 and roles_changed["rows"][0]["new_values"]["roles"] == ["Auditors"]
        assert (await c.get("/api/audit/events", params={"q": "crafted"})).json()["total"] == 1
        facets = (await c.get("/api/audit/events/facets")).json()
        assert {"role.created", "role.updated", "user.roles_changed"} <= set(facets["actions"])

        export = await c.get("/api/audit/events/export", params={"action": "settings.updated"})
        assert export.status_code == 200 and export.headers["content-type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(export.text)))
        assert rows[0][:3] == ["created_at", "action", "actor_email"]
        assert rows[1][2].startswith("'=HYPERLINK")  # formula neutralised
    assert len(await _events(tenant.id, "audit.exported")) == 1

    async with _client(tenant.id, resolve_role_permissions(DEFAULT_ROLES["Viewer"][1]), member.id) as viewer:
        assert (await viewer.get("/api/audit/events")).status_code == 403
        assert (await viewer.get("/api/audit/events/export")).status_code == 403

    async with AsyncSessionLocal() as db:
        other = Tenant(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(other)
        await db.commit()
    try:
        async with _client(other.id, ADMIN, uuid.UUID(int=0)) as outsider:
            assert (await outsider.get("/api/audit/events")).json()["total"] == 0
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(Tenant).where(Tenant.id == other.id))
            await db.commit()
