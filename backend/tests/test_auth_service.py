"""
Integration tests for AuthService against the real configured Postgres
(same convention as the rest of the suite - see tests/conftest.py; there is
no SQLite substitute).

Exercises the full login/refresh/logout flow, per-tenant RBAC provisioning,
role-derived permissions in the minted token, cross-tenant isolation, and
refresh-token rotation/revocation.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core import auth
from app.core.permissions import ALL_PERMISSION_CODES, PERMISSION_CATALOG
from app.core.db import AsyncSessionLocal
from app.models.permission import Permission
from app.models.tenant import Tenant
from app.repositories.permission_repository import PermissionRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService, Conflict, InvalidCredentials, InvalidRefreshToken


def _service(db) -> AuthService:
    return AuthService(
        users=UserRepository(db),
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
    )


@pytest_asyncio.fixture
async def seeded_permissions():
    """Ensure the permission catalog exists (idempotent). The migration seeds
    it in a real deployment; this keeps the test independent of migration
    state. Permissions are global (not tenant-scoped), so we leave them in
    place on teardown."""
    async with AsyncSessionLocal() as db:
        existing = set(
            (await db.execute(select(Permission.code))).scalars().all()
        )
        for code, desc in PERMISSION_CATALOG:
            if code not in existing:
                db.add(Permission(code=code, description=desc))
        await db.commit()


@pytest_asyncio.fixture
async def second_tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Other Tenant", slug=f"other-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        from sqlalchemy import delete

        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


async def _create_owner(db, tenant_id, email="owner@example.com", password="password123"):
    svc = _service(db)
    await svc.provision_tenant_rbac(tenant_id)
    owner_role = await svc.roles.get_by_name(tenant_id, "Owner")
    user = await svc.create_user(
        tenant_id=tenant_id, email=email, password=password,
        full_name="Owner", is_superuser=False, role_ids=[owner_role.id],
    )
    await db.commit()
    return user


async def test_provision_tenant_rbac_is_idempotent(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        svc = _service(db)
        first = await svc.provision_tenant_rbac(tenant.id)
        await db.commit()
        second = await svc.provision_tenant_rbac(tenant.id)
        await db.commit()
    assert {r.name for r in first} == {"Owner", "Admin", "Operator", "Viewer"}
    assert second == []  # already provisioned -> nothing new


async def test_owner_login_yields_all_permissions(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id)

    async with AsyncSessionLocal() as db:
        svc = _service(db)
        access, refresh, expires_in, resolved_tenant_id = await svc.login(
            tenant_id=tenant.id, email="owner@example.com", password="password123"
        )
        await db.commit()

    assert expires_in > 0
    assert resolved_tenant_id == tenant.id
    payload = auth._decode_access_token(access)
    assert set(payload["perms"]) == set(ALL_PERMISSION_CODES)
    assert payload["tid"] == str(tenant.id)


async def test_login_wrong_password_rejected(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id)
    async with AsyncSessionLocal() as db:
        with pytest.raises(InvalidCredentials):
            await _service(db).login(
                tenant_id=tenant.id, email="owner@example.com", password="WRONG"
            )


async def test_cross_tenant_login_isolation(seeded_permissions, tenant, second_tenant):
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id, email="owner@example.com")
    # Same email/password but wrong tenant id must fail (user not in that tenant).
    async with AsyncSessionLocal() as db:
        with pytest.raises(InvalidCredentials):
            await _service(db).login(
                tenant_id=second_tenant.id, email="owner@example.com", password="password123"
            )


# ---- Tenant-less login (email-resolved tenant) -----------------------------
# A real end user never knows their tenant UUID, so /auth/login accepts an
# omitted tenant_id and resolves it from a global (cross-tenant) email
# lookup. These tests cover the three possible resolutions: unique match
# (succeeds), no match (generic failure), and ambiguous match across more
# than one tenant (also a generic failure - never guesses, never leaks which
# case occurred).

async def test_tenantless_login_resolves_unique_email(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id, email="solo@example.com")

    async with AsyncSessionLocal() as db:
        access, refresh, expires_in, resolved_tenant_id = await _service(db).login(
            tenant_id=None, email="solo@example.com", password="password123"
        )
        await db.commit()

    assert expires_in > 0
    assert resolved_tenant_id == tenant.id
    payload = auth._decode_access_token(access)
    assert payload["tid"] == str(tenant.id)


async def test_tenantless_login_unknown_email_rejected(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        with pytest.raises(InvalidCredentials):
            await _service(db).login(
                tenant_id=None, email="nobody-registered@example.com", password="whatever123"
            )


async def test_tenantless_login_ambiguous_email_across_tenants_rejected(
    seeded_permissions, tenant, second_tenant
):
    # The SAME email registered as the Owner under two different
    # organizations - a legitimate but rare B2B edge case. Without an
    # explicit tenant_id there is no safe way to pick one, so this must
    # fail exactly like a wrong password would (never silently pick either
    # tenant, never reveal that the email is ambiguous vs. simply unknown).
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id, email="shared@example.com")
    async with AsyncSessionLocal() as db:
        await _create_owner(db, second_tenant.id, email="shared@example.com")

    async with AsyncSessionLocal() as db:
        with pytest.raises(InvalidCredentials):
            await _service(db).login(
                tenant_id=None, email="shared@example.com", password="password123"
            )


async def test_explicit_tenant_id_still_works_after_tenantless_login_added(
    seeded_permissions, tenant, second_tenant
):
    # Regression guard: adding the tenant-less path must not break a caller
    # that still supplies tenant_id explicitly (e.g. a saved session, or the
    # superuser cross-tenant flow) - even when the same email is ambiguous
    # globally, an explicit tenant_id must resolve deterministically to that
    # tenant's user, not fall through to the ambiguous-email rejection.
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id, email="shared2@example.com")
    async with AsyncSessionLocal() as db:
        await _create_owner(db, second_tenant.id, email="shared2@example.com")

    async with AsyncSessionLocal() as db:
        access, refresh, expires_in, resolved_tenant_id = await _service(db).login(
            tenant_id=tenant.id, email="shared2@example.com", password="password123"
        )
        await db.commit()
    assert resolved_tenant_id == tenant.id


async def test_duplicate_user_rejected(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id, email="dup@example.com")
    async with AsyncSessionLocal() as db:
        svc = _service(db)
        with pytest.raises(Conflict):
            await svc.create_user(
                tenant_id=tenant.id, email="dup@example.com", password="password123",
                full_name=None, is_superuser=False, role_ids=[],
            )


async def test_refresh_rotation_revokes_old_token(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id)
    async with AsyncSessionLocal() as db:
        svc = _service(db)
        _, refresh1, _, _ = await svc.login(
            tenant_id=tenant.id, email="owner@example.com", password="password123"
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        svc = _service(db)
        _, refresh2, _ = await svc.refresh(refresh_token=refresh1)
        await db.commit()
    assert refresh2 != refresh1

    # Re-using the rotated-out token must now fail.
    async with AsyncSessionLocal() as db:
        with pytest.raises(InvalidRefreshToken):
            await _service(db).refresh(refresh_token=refresh1)


async def test_logout_revokes_refresh_token(seeded_permissions, tenant):
    async with AsyncSessionLocal() as db:
        await _create_owner(db, tenant.id)
    async with AsyncSessionLocal() as db:
        svc = _service(db)
        _, refresh_token, _, _ = await svc.login(
            tenant_id=tenant.id, email="owner@example.com", password="password123"
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        svc = _service(db)
        await svc.logout(refresh_token=refresh_token)
        await db.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(InvalidRefreshToken):
            await _service(db).refresh(refresh_token=refresh_token)
