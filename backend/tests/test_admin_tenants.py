"""
Platform-admin tenant read API tests (Phase 6.3C §13).

Covers the superuser gate, the explicit-tenant contract, canonical billing
values, discovery scopes, honest nulls for unsupported profile fields and the
absence of credential material.

Runs against the real Postgres DB per project convention. Authorization is never
mocked: the negative cases use a genuinely signed NON-superuser token.
"""
from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.permission import Permission
from app.models.role import Role, role_permissions, user_roles
from app.models.tenant import Tenant
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "http://test"
UNKNOWN_TENANT = "00000000-0000-0000-0000-000000000000"

#: Profile fields the canonical Tenant model does not carry. They must come back
#: as null rather than being invented by the API.
UNSUPPORTED_PROFILE_FIELDS = (
    "display_name",
    "legal_name",
    "industry",
    "country",
    "timezone",
    "locale",
    "contacts",
    "branding",
    "custom_properties",
)


async def _make_tenant() -> tuple[Tenant, User]:
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="Admin API Test", slug=f"adm-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        user = User(
            tenant_id=tenant.id,
            email=f"adm-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="test-hash",
            full_name="Admin API Actor",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return tenant, user


async def _drop(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(User.__table__.delete().where(User.tenant_id == tenant_id))
        await db.execute(Tenant.__table__.delete().where(Tenant.id == tenant_id))
        await db.commit()


def _token(user: User, *, superuser: bool) -> str:
    return create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        is_superuser=superuser,
        permissions=frozenset({"devices.read"} if superuser else set()),
    )


async def _get(path: str, token: str) -> httpx.Response:
    async with httpx.AsyncClient(app=app, base_url=BASE) as client:
        return await client.get(path, headers={"Authorization": f"Bearer {token}"})


class TestAdminTenantAuthorization:
    async def test_superuser_can_list_and_read(self):
        tenant, user = await _make_tenant()
        try:
            admin = _token(user, superuser=True)
            listed = await _get("/api/admin/tenants", admin)
            assert listed.status_code == 200, listed.text
            ids = [row["tenant_id"] for row in listed.json()]
            assert str(tenant.id) in ids

            detail = await _get(f"/api/admin/tenants/{tenant.id}", admin)
            assert detail.status_code == 200, detail.text
            assert detail.json()["profile"]["tenant_id"] == str(tenant.id)
        finally:
            await _drop(tenant.id)

    async def test_non_superuser_is_forbidden(self):
        """A real signed non-superuser token must be refused by the backend."""
        tenant, user = await _make_tenant()
        try:
            mortal = _token(user, superuser=False)
            assert (await _get("/api/admin/tenants", mortal)).status_code == 403
            assert (
                await _get(f"/api/admin/tenants/{tenant.id}", mortal)
            ).status_code == 403
        finally:
            await _drop(tenant.id)

    async def test_unknown_tenant_is_404(self):
        tenant, user = await _make_tenant()
        try:
            admin = _token(user, superuser=True)
            resp = await _get(f"/api/admin/tenants/{UNKNOWN_TENANT}", admin)
            assert resp.status_code == 404
        finally:
            await _drop(tenant.id)


class TestAdminTenantDetailShape:
    async def test_unsupported_profile_fields_are_null(self):
        """The API must not invent tenant properties the model lacks."""
        tenant, user = await _make_tenant()
        try:
            admin = _token(user, superuser=True)
            body = (await _get(f"/api/admin/tenants/{tenant.id}", admin)).json()
            for field in UNSUPPORTED_PROFILE_FIELDS:
                assert body["profile"][field] is None, field
        finally:
            await _drop(tenant.id)

    async def test_canonical_sections_present(self):
        tenant, user = await _make_tenant()
        try:
            admin = _token(user, superuser=True)
            body = (await _get(f"/api/admin/tenants/{tenant.id}", admin)).json()
            for key in (
                "profile",
                "subscription",
                "usage",
                "counts",
                "last_activity_at",
                "discovery_scopes",
            ):
                assert key in body, key
            # Counts are computed from real rows, and a fresh tenant is a real 0.
            assert body["counts"]["users_total"] == 1
            assert body["counts"]["users_active"] == 1
            assert body["counts"]["devices_total"] == 0
            assert body["counts"]["discovery_scopes_total"] == 0
            assert body["discovery_scopes"] == []
        finally:
            await _drop(tenant.id)

    async def test_usage_entries_are_canonical_when_present(self):
        """When entitlements resolve, every usage entry carries limit/used/pct."""
        tenant, user = await _make_tenant()
        try:
            admin = _token(user, superuser=True)
            body = (await _get(f"/api/admin/tenants/{tenant.id}", admin)).json()
            for resource, entry in body["usage"].items():
                assert set(entry) == {"label", "used", "limit", "pct", "over_limit"}, resource
                # Values must be numbers, never strings or nulls.
                assert isinstance(entry["used"], int)
                assert isinstance(entry["limit"], int)
                assert isinstance(entry["pct"], (int, float))
                assert isinstance(entry["over_limit"], bool)
        finally:
            await _drop(tenant.id)

    async def test_no_secret_material_in_response(self):
        tenant, user = await _make_tenant()
        try:
            admin = _token(user, superuser=True)
            resp = await _get(f"/api/admin/tenants/{tenant.id}", admin)
            blob = json.dumps(resp.json()).lower()
            for secret in (
                "password",
                "community",
                "api_key",
                "apikey",
                "secret",
                "private_key",
                "redis://",
                "postgresql://",
            ):
                assert secret not in blob, secret
        finally:
            await _drop(tenant.id)


class TestAdminTenantRbac:
    """Phase 6.3D - read-only RBAC, tenant-scoped."""

    async def test_empty_role_state_is_an_empty_list(self):
        """A tenant with no roles returns [], not a fabricated role."""
        tenant, user = await _make_tenant()
        try:
            admin = _token(user, superuser=True)
            body = (await _get(f"/api/admin/tenants/{tenant.id}", admin)).json()
            assert body["roles"] == []
        finally:
            await _drop(tenant.id)

    async def test_tenant_roles_and_permissions_are_returned(self):
        """A seeded role comes back with its real permission keys."""
        tenant, user = await _make_tenant()
        permission_id = None
        role_id = None
        try:
            async with AsyncSessionLocal() as db:
                permission = (
                    await db.execute(
                        select(Permission).where(Permission.code == "devices.read").limit(1)
                    )
                ).scalar_one_or_none()
                if permission is None:
                    permission = Permission(
                        code="devices.read", description="Read devices"
                    )
                    db.add(permission)
                    await db.flush()
                permission_id = permission.id

                role = Role(
                    tenant_id=tenant.id,
                    name=f"RBAC Test Role {uuid.uuid4().hex[:6]}",
                    description="read-only test role",
                )
                role.permissions.append(permission)
                db.add(role)
                await db.commit()
                await db.refresh(role)
                role_id = role.id

            admin = _token(user, superuser=True)
            body = (await _get(f"/api/admin/tenants/{tenant.id}", admin)).json()
            assert len(body["roles"]) == 1
            role_out = body["roles"][0]
            assert role_out["name"] == role.name
            assert role_out["scope"] == "tenant"
            assert role_out["user_count"] == 0
            keys = [p["key"] for p in role_out["permissions"]]
            assert "devices.read" in keys
            # Field shape matches the documented contract.
            assert set(role_out["permissions"][0]) == {"id", "key", "name", "description"}
        finally:
            async with AsyncSessionLocal() as db:
                if role_id:
                    await db.execute(
                        role_permissions.delete().where(
                            role_permissions.c.role_id == role_id
                        )
                    )
                    await db.execute(Role.__table__.delete().where(Role.id == role_id))
                await db.commit()
            await _drop(tenant.id)

    async def test_roles_do_not_leak_across_tenants(self):
        """Tenant B's role must never appear in Tenant A's detail response."""
        tenant_a, user_a = await _make_tenant()
        tenant_b, _user_b = await _make_tenant()
        role_b_id = None
        try:
            async with AsyncSessionLocal() as db:
                role_b = Role(
                    tenant_id=tenant_b.id,
                    name=f"B-Only Role {uuid.uuid4().hex[:6]}",
                    description="belongs to tenant B",
                )
                db.add(role_b)
                await db.commit()
                await db.refresh(role_b)
                role_b_id = role_b.id

            admin = _token(user_a, superuser=True)
            body = (await _get(f"/api/admin/tenants/{tenant_a.id}", admin)).json()
            names = [r["name"] for r in body["roles"]]
            assert role_b.name not in names
            assert body["roles"] == []

            # And tenant B's own view does contain it.
            body_b = (await _get(f"/api/admin/tenants/{tenant_b.id}", admin)).json()
            assert role_b.name in [r["name"] for r in body_b["roles"]]
        finally:
            async with AsyncSessionLocal() as db:
                if role_b_id:
                    await db.execute(Role.__table__.delete().where(Role.id == role_b_id))
                await db.commit()
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)

    async def test_rbac_requires_superuser(self):
        tenant, user = await _make_tenant()
        try:
            mortal = _token(user, superuser=False)
            assert (
                await _get(f"/api/admin/tenants/{tenant.id}", mortal)
            ).status_code == 403
        finally:
            await _drop(tenant.id)


class TestAdminTenantUsers:
    """Phase 6.3D follow-up - read-only tenant users."""

    async def test_users_are_returned_with_role_names(self):
        tenant, actor = await _make_tenant()
        role_id = None
        try:
            async with AsyncSessionLocal() as db:
                role = Role(
                    tenant_id=tenant.id,
                    name=f"Users Test Role {uuid.uuid4().hex[:6]}",
                )
                db.add(role)
                await db.commit()
                await db.refresh(role)
                role_id = role.id

                target = (
                    await db.execute(select(User).where(User.id == actor.id))
                ).scalar_one()
                # Insert the association row directly: lazy-loading
                # User.roles is not available in an async context, and forcing
                # it would make this test about SQLAlchemy rather than the API.
                await db.execute(
                    user_roles.insert().values(user_id=target.id, role_id=role.id)
                )
                await db.commit()

            admin = _token(actor, superuser=True)
            body = (await _get(f"/api/admin/tenants/{tenant.id}", admin)).json()
            assert body["users"]["total"] >= 1
            assert body["users"]["truncated"] is False
            row = next(u for u in body["users"]["items"] if u["id"] == str(actor.id))
            assert row["email"] == actor.email
            assert role.name in row["roles"]
            assert set(row) == {
                "id",
                "email",
                "full_name",
                "is_active",
                "is_superuser",
                "last_login_at",
                "created_at",
                "roles",
            }
        finally:
            async with AsyncSessionLocal() as db:
                if role_id:
                    await db.execute(
                        user_roles.delete().where(user_roles.c.role_id == role_id)
                    )
                    await db.execute(Role.__table__.delete().where(Role.id == role_id))
                await db.commit()
            await _drop(tenant.id)

    async def test_password_hash_never_appears(self):
        """The single most important guarantee: no credential column is exposed."""
        tenant, actor = await _make_tenant()
        try:
            admin = _token(actor, superuser=True)
            resp = await _get(f"/api/admin/tenants/{tenant.id}", admin)
            blob = json.dumps(resp.json()).lower()
            for forbidden in (
                "password_hash",
                "password",
                "hashed_password",
                "salt",
                "refresh_token",
            ):
                assert forbidden not in blob, forbidden
        finally:
            await _drop(tenant.id)

    async def test_users_do_not_leak_across_tenants(self):
        tenant_a, actor_a = await _make_tenant()
        tenant_b, actor_b = await _make_tenant()
        try:
            admin = _token(actor_a, superuser=True)
            body_a = (await _get(f"/api/admin/tenants/{tenant_a.id}", admin)).json()
            emails_a = [u["email"] for u in body_a["users"]["items"]]
            assert actor_b.email not in emails_a
            assert actor_a.email in emails_a

            body_b = (await _get(f"/api/admin/tenants/{tenant_b.id}", admin)).json()
            emails_b = [u["email"] for u in body_b["users"]["items"]]
            assert actor_a.email not in emails_b
            assert actor_b.email in emails_b
        finally:
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)

    async def test_users_require_superuser(self):
        tenant, actor = await _make_tenant()
        try:
            mortal = _token(actor, superuser=False)
            assert (
                await _get(f"/api/admin/tenants/{tenant.id}", mortal)
            ).status_code == 403
        finally:
            await _drop(tenant.id)
