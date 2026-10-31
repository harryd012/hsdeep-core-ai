"""
P0 tenant-context tests (DB-backed, same convention as the rest of the suite —
see tests/conftest.py; no SQLite substitute).

Covers the new tenant-context milestone:
  * TenantRepository: authoritative tenant row lookups (existence + soft-delete).
  * The superuser X-Tenant-Id override target validation (P0 security hardening):
    a real/live tenant passes; a non-existent or soft-deleted tenant is rejected.
  * GET /api/auth/me accessible_tenants: a normal user sees exactly their own
    tenant (with their role); a superuser sees every live tenant; the active
    tenant name is the authoritative DB row, never token-invented.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core import auth
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.permission import Permission
from app.models.tenant import Tenant
from app.repositories.permission_repository import PermissionRepository
from app.repositories.refresh_token_repository import RefreshTokenRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.core.permissions import PERMISSION_CATALOG


def _service(db) -> AuthService:
    return AuthService(
        users=UserRepository(db),
        roles=RoleRepository(db),
        permissions=PermissionRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
    )


@pytest_asyncio.fixture
async def seeded_permissions():
    async with AsyncSessionLocal() as db:
        existing = set((await db.execute(select(Permission.code))).scalars().all())
        for code, desc in PERMISSION_CATALOG:
            if code not in existing:
                db.add(Permission(code=code, description=desc))
        await db.commit()


@pytest_asyncio.fixture
async def tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Test Tenant", slug=f"test-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
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
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


# ---------------------------------------------------------------------------
# TenantRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_existing_returns_live_tenant(tenant):
    async with AsyncSessionLocal() as db:
        row = await TenantRepository(db).get_existing(tenant.id)
    assert row is not None
    assert row.id == tenant.id
    assert row.name == "Test Tenant"


@pytest.mark.asyncio
async def test_get_existing_excludes_soft_deleted(tenant):
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.id == tenant.id))).scalar_one()
        t.is_deleted = True
        await db.commit()
        assert await TenantRepository(db).get_existing(tenant.id) is None


@pytest.mark.asyncio
async def test_get_existing_returns_none_for_nonexistent():
    async with AsyncSessionLocal() as db:
        assert await TenantRepository(db).get_existing(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_list_all_excludes_soft_deleted(tenant, second_tenant):
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.id == second_tenant.id))).scalar_one()
        t.is_deleted = True
        await db.commit()
        rows = await TenantRepository(db).list_all()
    ids = {t.id for t in rows}
    assert tenant.id in ids
    assert second_tenant.id not in ids


# ---------------------------------------------------------------------------
# Override target validation (P0 security hardening)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_override_tenant_passes_for_live_tenant(tenant):
    await auth._validate_override_tenant(tenant.id)


@pytest.mark.asyncio
async def test_validate_override_tenant_passes_for_suspended_tenant(tenant):
    # Suspended (is_active=False) tenants stay administrable — only
    # soft-deleted/non-existent tenants are rejected.
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.id == tenant.id))).scalar_one()
        t.is_active = False
        await db.commit()
    await auth._validate_override_tenant(tenant.id)


@pytest.mark.asyncio
async def test_validate_override_tenant_rejects_nonexistent():
    with pytest.raises(Exception) as exc:
        await auth._validate_override_tenant(uuid.uuid4())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_validate_override_tenant_rejects_soft_deleted(tenant):
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.id == tenant.id))).scalar_one()
        t.is_deleted = True
        await db.commit()
    with pytest.raises(Exception) as exc:
        await auth._validate_override_tenant(tenant.id)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/auth/me — accessible_tenants
# ---------------------------------------------------------------------------


def _make_superuser_token(tenant_id: uuid.UUID) -> str:
    return auth.create_access_token(
        user_id=uuid.uuid4(), tenant_id=tenant_id, email="su@x.com",
        is_superuser=True, permissions=frozenset(),
    )


def _make_user_token(
    tenant_id: uuid.UUID,
    perms=("dashboard.read",),
    *,
    user_id: uuid.UUID | None = None,
) -> str:
    """Mint a normal-user token.

    ``user_id`` defaults to a random id for tests that only exercise
    authorization. Pass a REAL user id when the assertion depends on
    /api/auth/me's tenant context: that context is deliberately fail-closed and
    is derived from the authoritative ``users`` row, never from the token.
    """
    return auth.create_access_token(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        email="u@x.com",
        is_superuser=False,
        permissions=frozenset(perms),
    )


@pytest.mark.asyncio
async def test_me_normal_user_sees_only_own_tenant(tenant, seeded_permissions):
    # /api/auth/me resolves the tenant context from the authoritative `users`
    # row and is fail-closed when that row is missing, so this test must mint
    # its token for a REAL user of the fixture tenant. (A token referencing a
    # non-existent user id correctly yields tenant_name=None - that is the
    # fail-closed branch, exercised separately, not here.)
    async with AsyncSessionLocal() as db:
        svc = _service(db)
        await svc.provision_tenant_rbac(tenant.id)
        owner_role = await svc.roles.get_by_name(tenant.id, "Owner")
        user = await svc.create_user(
            tenant_id=tenant.id,
            email="me-viewer@example.com",
            password="password123",
            full_name="Viewer",
            is_superuser=False,
            role_ids=[owner_role.id],
        )
        await db.commit()
        real_user_id = user.id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(
            "/api/auth/me",
            headers={
                "Authorization": f"Bearer {_make_user_token(tenant.id, user_id=real_user_id)}"
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == str(tenant.id)
    assert body["tenant_name"] == "Test Tenant"
    assert len(body["accessible_tenants"]) == 1
    own = body["accessible_tenants"][0]
    assert own["id"] == str(tenant.id)
    assert own["name"] == "Test Tenant"


@pytest.mark.asyncio
async def test_me_superuser_sees_all_live_tenants(tenant, second_tenant, seeded_permissions):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {_make_superuser_token(tenant.id)}"})
    assert resp.status_code == 200
    body = resp.json()
    ids = {t["id"] for t in body["accessible_tenants"]}
    assert str(tenant.id) in ids
    assert str(second_tenant.id) in ids
    for t in body["accessible_tenants"]:
        assert t["role"] == "Superuser"


@pytest.mark.asyncio
async def test_me_superuser_excludes_soft_deleted_tenant(tenant, second_tenant, seeded_permissions):
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.id == second_tenant.id))).scalar_one()
        t.is_deleted = True
        await db.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {_make_superuser_token(tenant.id)}"})
    body = resp.json()
    ids = {t["id"] for t in body["accessible_tenants"]}
    assert str(tenant.id) in ids
    assert str(second_tenant.id) not in ids


@pytest.mark.asyncio
async def test_me_override_validates_target(tenant, second_tenant, seeded_permissions):
    # A superuser targeting a NON-EXISTENT tenant via X-Tenant-Id must be
    # rejected (P0 hardening) — previously silently accepted.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(
            "/api/auth/me",
            headers={
                "Authorization": f"Bearer {_make_superuser_token(tenant.id)}",
                "X-Tenant-Id": str(uuid.uuid4()),
            },
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_me_override_to_live_tenant_succeeds(tenant, second_tenant, seeded_permissions):
    # A superuser holding a break-glass grant for a real live tenant can target
    # it via X-Tenant-Id, and the active tenant name reflects the override.
    from app.core.db import AsyncSessionLocal
    from app.models.user import User
    from app.services.support_access import SupportAccessService

    async with AsyncSessionLocal() as db:
        su = User(tenant_id=tenant.id, email=f"su-{uuid.uuid4().hex[:6]}@x.com", is_superuser=True)
        db.add(su)
        await db.flush()
        await SupportAccessService(db).create(
            user_id=su.id, user_email=su.email, home_tenant_id=tenant.id, tenant_id=second_tenant.id,
            reason="Investigating ticket 4711 for the customer", duration_minutes=30,
        )
        await db.commit()
    token = auth.create_access_token(user_id=su.id, tenant_id=tenant.id, email=su.email,
                                     is_superuser=True, permissions=frozenset())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        resp = await client.get(
            "/api/auth/me",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": str(second_tenant.id),
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == str(second_tenant.id)
    assert body["tenant_name"] == "Other Tenant"