"""
Tenant rename mutation tests (Phase 6.3F §1).

Real DB, real signed tokens. Authorization, tenant scoping and the audit trail
are never mocked: the point of this phase is that a rename is gated, scoped,
validated and recorded with before/after values.

Throwaway tenants/users are created and deleted here, so existing tenant data is
never touched.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.tenant import Tenant
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "http://test"


async def _make_tenant(name: str = "Rename Target") -> tuple[Tenant, User]:
    async with AsyncSessionLocal() as db:
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant(name=name, slug=f"rn-{suffix}")
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)
        user = User(
            tenant_id=tenant.id,
            email=f"rn-{suffix}@example.com",
            password_hash="test-hash",
            full_name="Rename Actor",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return tenant, user


async def _drop(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM audit_events WHERE tenant_id = :t"), {"t": tenant_id}
        )
        await db.execute(User.__table__.delete().where(User.tenant_id == tenant_id))
        await db.execute(Tenant.__table__.delete().where(Tenant.id == tenant_id))
        await db.commit()


def _token(user: User, *, superuser: bool = True) -> str:
    return create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        is_superuser=superuser,
        permissions=frozenset({"devices.write"}) if superuser else frozenset(),
    )


async def _patch(tenant_id: uuid.UUID, token: str, body: dict):
    async with httpx.AsyncClient(app=app, base_url=BASE) as client:
        return await client.patch(
            f"/api/admin/tenants/{tenant_id}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )


async def _read_tenant(tenant_id: uuid.UUID) -> tuple[str, str]:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text("SELECT name, slug FROM tenants WHERE id = :id"), {"id": tenant_id}
            )
        ).one()
    return row[0], row[1]


class TestRename:
    async def test_rename_name_returns_before_after_and_persists(self):
        tenant, admin = await _make_tenant("Old Name")
        try:
            resp = await _patch(tenant.id, _token(admin), {"name": "New Name"})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["changed"] is True
            assert body["before"] == {"name": "Old Name", "slug": tenant.slug}
            assert body["after"] == {"name": "New Name", "slug": tenant.slug}
            # The change is real, not just echoed back.
            assert await _read_tenant(tenant.id) == ("New Name", tenant.slug)
        finally:
            await _drop(tenant.id)

    async def test_rename_name_and_slug_together(self):
        tenant, admin = await _make_tenant("Both Fields")
        new_slug = f"rn-{uuid.uuid4().hex[:8]}"
        try:
            resp = await _patch(
                tenant.id, _token(admin), {"name": "Renamed Both", "slug": new_slug}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["after"] == {"name": "Renamed Both", "slug": new_slug}
            assert await _read_tenant(tenant.id) == ("Renamed Both", new_slug)
        finally:
            await _drop(tenant.id)

    async def test_name_is_trimmed(self):
        tenant, admin = await _make_tenant("Untrimmed")
        try:
            resp = await _patch(tenant.id, _token(admin), {"name": "   Trimmed   "})
            assert resp.status_code == 200, resp.text
            assert resp.json()["after"]["name"] == "Trimmed"
        finally:
            await _drop(tenant.id)

    async def test_noop_rename_writes_nothing_and_no_audit(self):
        """An identical value must not pollute the audit trail."""
        tenant, admin = await _make_tenant("Same Name")
        try:
            resp = await _patch(
                tenant.id, _token(admin), {"name": "Same Name", "slug": tenant.slug}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["changed"] is False
            async with AsyncSessionLocal() as db:
                count = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM audit_events WHERE tenant_id = :t "
                            "AND action = 'tenant.renamed'"
                        ),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            assert count == 0
        finally:
            await _drop(tenant.id)


class TestAudit:
    async def test_audit_records_canonical_before_and_after(self):
        tenant, admin = await _make_tenant("Audit Old")
        try:
            resp = await _patch(tenant.id, _token(admin), {"name": "Audit New"})
            assert resp.status_code == 200, resp.text

            async with AsyncSessionLocal() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT action, actor_id, entity_type, entity_id, "
                            "old_values, new_values FROM audit_events "
                            "WHERE tenant_id = :t AND action = 'tenant.renamed'"
                        ),
                        {"t": tenant.id},
                    )
                ).one()
            assert row[0] == "tenant.renamed"
            assert str(row[1]) == str(admin.id)          # actor
            assert row[2] == "tenant"
            assert str(row[3]) == str(tenant.id)
            assert row[4]["name"] == "Audit Old"          # before
            assert row[5]["name"] == "Audit New"          # after
            assert row[4]["slug"] == tenant.slug
        finally:
            await _drop(tenant.id)

    async def test_audit_is_tenant_scoped(self):
        """The event belongs to the renamed tenant, not the actor's tenant."""
        tenant, admin = await _make_tenant("Scoped Audit")
        try:
            await _patch(tenant.id, _token(admin), {"name": "Scoped Renamed"})
            async with AsyncSessionLocal() as db:
                wrong = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM audit_events "
                            "WHERE action = 'tenant.renamed' AND tenant_id <> :t"
                        ),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            assert wrong == 0
        finally:
            await _drop(tenant.id)


class TestValidation:
    async def test_empty_payload_rejected(self):
        tenant, admin = await _make_tenant("Empty Payload")
        try:
            assert (await _patch(tenant.id, _token(admin), {})).status_code == 422
        finally:
            await _drop(tenant.id)

    async def test_empty_or_whitespace_name_rejected(self):
        tenant, admin = await _make_tenant("Blank Name")
        try:
            for bad in ("", "   "):
                resp = await _patch(tenant.id, _token(admin), {"name": bad})
                assert resp.status_code == 422, (bad, resp.text)
        finally:
            await _drop(tenant.id)

    async def test_overlong_name_rejected(self):
        tenant, admin = await _make_tenant("Long Name")
        try:
            resp = await _patch(tenant.id, _token(admin), {"name": "x" * 256})
            assert resp.status_code == 422, resp.text
        finally:
            await _drop(tenant.id)

    async def test_control_characters_in_name_rejected(self):
        tenant, admin = await _make_tenant("Control Chars")
        try:
            resp = await _patch(tenant.id, _token(admin), {"name": "bad\nname"})
            assert resp.status_code == 422, resp.text
        finally:
            await _drop(tenant.id)

    async def test_invalid_slug_rejected(self):
        tenant, admin = await _make_tenant("Bad Slug")
        try:
            for bad in ("UPPER", "has space", "trailing-", "-leading", "a..b", "a/b", ""):
                resp = await _patch(tenant.id, _token(admin), {"slug": bad})
                assert resp.status_code == 422, (bad, resp.text)
            # The rejected attempts changed nothing.
            assert await _read_tenant(tenant.id) == ("Bad Slug", tenant.slug)
        finally:
            await _drop(tenant.id)

    async def test_unknown_field_rejected(self):
        """extra="forbid" - a typo must not be silently ignored on a mutation."""
        tenant, admin = await _make_tenant("Extra Field")
        try:
            resp = await _patch(
                tenant.id, _token(admin), {"name": "Ok", "is_active": False}
            )
            assert resp.status_code == 422, resp.text
        finally:
            await _drop(tenant.id)

    async def test_slug_conflict_returns_409(self):
        tenant_a, admin = await _make_tenant("Slug Owner A")
        tenant_b, _ = await _make_tenant("Slug Owner B")
        try:
            resp = await _patch(tenant_a.id, _token(admin), {"slug": tenant_b.slug})
            assert resp.status_code == 409, resp.text
            assert tenant_b.slug in resp.text
            # Neither tenant was altered.
            assert await _read_tenant(tenant_a.id) == ("Slug Owner A", tenant_a.slug)
            assert await _read_tenant(tenant_b.id) == ("Slug Owner B", tenant_b.slug)
        finally:
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)


class TestAuthorizationAndScope:
    async def test_non_superuser_is_forbidden(self):
        tenant, user = await _make_tenant("Forbidden")
        try:
            resp = await _patch(tenant.id, _token(user, superuser=False), {"name": "Nope"})
            assert resp.status_code == 403, resp.text
            # Rejected, so nothing was renamed and nothing was audited.
            assert await _read_tenant(tenant.id) == ("Forbidden", tenant.slug)
            async with AsyncSessionLocal() as db:
                count = (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM audit_events "
                            "WHERE tenant_id = :t AND action = 'tenant.renamed'"
                        ),
                        {"t": tenant.id},
                    )
                ).scalar_one()
            assert count == 0
        finally:
            await _drop(tenant.id)

    async def test_unknown_tenant_is_404(self):
        tenant, admin = await _make_tenant("Unknown Target")
        try:
            resp = await _patch(uuid.uuid4(), _token(admin), {"name": "Ghost"})
            assert resp.status_code == 404, resp.text
        finally:
            await _drop(tenant.id)

    async def test_soft_deleted_tenant_is_404(self):
        """A deleted tenant must not be mutable."""
        tenant, admin = await _make_tenant("Deleted Tenant")
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "UPDATE tenants SET is_deleted = true, deleted_at = now() "
                        "WHERE id = :id"
                    ),
                    {"id": tenant.id},
                )
                await db.commit()
            resp = await _patch(tenant.id, _token(admin), {"name": "Zombie"})
            assert resp.status_code == 404, resp.text
        finally:
            await _drop(tenant.id)

    async def test_renaming_one_tenant_leaves_others_untouched(self):
        """Cross-tenant safety: B is byte-identical after A is renamed."""
        tenant_a, admin = await _make_tenant("Alpha")
        tenant_b, _ = await _make_tenant("Beta")
        try:
            before_b = await _read_tenant(tenant_b.id)
            resp = await _patch(tenant_a.id, _token(admin), {"name": "Alpha Renamed"})
            assert resp.status_code == 200, resp.text
            assert await _read_tenant(tenant_b.id) == before_b
        finally:
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)
