"""
Unit tests for app.core.auth (no database).

Covers password hashing, access-token mint/verify/expiry, the type guard,
Principal permission logic, the superuser X-Tenant-Id override rule, and the
require_permission dependency's allow/deny behavior.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from jose import jwt

from app.core import auth
from app.core.config import get_settings

settings = get_settings()


def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret-password")
    assert h != "s3cret-password"
    assert auth.verify_password("s3cret-password", h) is True
    assert auth.verify_password("wrong", h) is False


def test_verify_password_none_or_malformed_hash_is_false():
    assert auth.verify_password("anything", None) is False
    assert auth.verify_password("anything", "not-a-real-hash") is False


def test_refresh_token_hash_is_stable_and_hex():
    tok = auth.generate_refresh_token()
    h1 = auth.hash_refresh_token(tok)
    h2 = auth.hash_refresh_token(tok)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_access_token_roundtrip_carries_claims():
    uid, tid = uuid.uuid4(), uuid.uuid4()
    token = auth.create_access_token(
        user_id=uid, tenant_id=tid, email="a@b.com", is_superuser=False,
        permissions=frozenset({"alerts.read", "devices.read"}),
    )
    payload = auth._decode_access_token(token)
    assert payload["sub"] == str(uid)
    assert payload["tid"] == str(tid)
    assert payload["type"] == "access"
    assert set(payload["perms"]) == {"alerts.read", "devices.read"}


def test_expired_access_token_rejected(monkeypatch):
    monkeypatch.setattr(auth.settings, "access_token_expire_minutes", -1)
    token = auth.create_access_token(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="a@b.com",
        is_superuser=False, permissions=frozenset(),
    )
    with pytest.raises(HTTPException) as exc:
        auth._decode_access_token(token)
    assert exc.value.status_code == 401


def test_non_access_token_type_rejected():
    bad = jwt.encode(
        {"sub": str(uuid.uuid4()), "tid": str(uuid.uuid4()), "type": "refresh"},
        settings.effective_jwt_secret, algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(HTTPException) as exc:
        auth._decode_access_token(bad)
    assert exc.value.status_code == 401


def test_principal_permission_logic():
    p = auth.Principal(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="a@b.com",
        is_superuser=False, permissions=frozenset({"alerts.read"}),
    )
    assert p.has_permission("alerts.read") is True
    assert p.has_permission("alerts.resolve") is False


def test_superuser_bypasses_permission_check():
    su = auth.Principal(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="a@b.com",
        is_superuser=True, permissions=frozenset(),
    )
    assert su.has_permission("anything.at.all") is True


def test_superuser_tenant_override_allowed():
    token_tenant, target_tenant = uuid.uuid4(), uuid.uuid4()
    payload = {"sub": str(uuid.uuid4()), "tid": str(token_tenant), "su": True, "perms": []}
    principal = auth._principal_from_payload(payload, target_tenant)
    assert principal.tenant_id == target_tenant


def test_normal_user_tenant_override_forbidden():
    token_tenant, target_tenant = uuid.uuid4(), uuid.uuid4()
    payload = {"sub": str(uuid.uuid4()), "tid": str(token_tenant), "su": False, "perms": []}
    with pytest.raises(HTTPException) as exc:
        auth._principal_from_payload(payload, target_tenant)
    assert exc.value.status_code == 403


async def test_require_permission_allows_and_denies():
    dep = auth.require_permission("alerts.read")
    allowed = auth.Principal(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="a@b.com",
        is_superuser=False, permissions=frozenset({"alerts.read"}),
    )
    # Returns the principal when permitted.
    assert await dep(principal=allowed) is allowed

    denied = auth.Principal(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), email="a@b.com",
        is_superuser=False, permissions=frozenset(),
    )
    with pytest.raises(HTTPException) as exc:
        await dep(principal=denied)
    assert exc.value.status_code == 403
