"""
Tenant lifecycle tests (Phase 6.4A): suspend / reactivate.

Real DB, real signed tokens. Authorization, tenant scoping, the audit trail and
data preservation are all verified against actual rows - never mocked.

Throwaway tenants are created and removed here, so existing tenant data is never
touched.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.discovery_scope import DiscoveryScope
from app.models.role import Role
from app.models.tenant import Tenant
from app.models.tenant_custom_property import TenantCustomProperty
from app.models.user import User

pytestmark = pytest.mark.asyncio

BASE = "http://test"


async def _make_tenant(
    name: str = "Lifecycle Target", *, active: bool = True, seed: bool = False
) -> tuple[Tenant, User]:
    """Create a throwaway tenant (+ actor). `seed` adds tenant-owned data so
    suspension can be proven not to delete it."""
    async with AsyncSessionLocal() as db:
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant(name=name, slug=f"lc-{suffix}", is_active=active)
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

        user = User(
            tenant_id=tenant.id,
            email=f"lc-{suffix}@example.com",
            password_hash="test-hash",
            full_name="Lifecycle Actor",
            is_active=True,
        )
        db.add(user)

        if seed:
            db.add(Role(tenant_id=tenant.id, name="Lifecycle Role"))
            db.add(
                DiscoveryScope(
                    tenant_id=tenant.id,
                    name="Lifecycle Scope",
                    scope_type="cidr",
                    target="10.42.0.0/24",
                )
            )
            db.add(
                TenantCustomProperty(
                    tenant_id=tenant.id,
                    key="lifecycle_key",
                    display_name="Lifecycle Property",
                    data_type="string",
                )
            )
        await db.commit()
        await db.refresh(user)
    return tenant, user


async def _counts(tenant_id: uuid.UUID) -> dict[str, int]:
    """Row counts for tenant-owned data, straight from the tables."""
    async with AsyncSessionLocal() as db:
        out: dict[str, int] = {}
        for table in (
            "users",
            "roles",
            "tenant_custom_properties",
            "discovery_scopes",
            "audit_events",
        ):
            out[table] = (
                await db.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            ).scalar_one()
        return out


async def _drop(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        for stmt in (
            "DELETE FROM audit_events WHERE tenant_id = :t",
            "DELETE FROM discovery_scopes WHERE tenant_id = :t",
            "DELETE FROM tenant_custom_properties WHERE tenant_id = :t",
            "DELETE FROM roles WHERE tenant_id = :t",
            "DELETE FROM users WHERE tenant_id = :t",
        ):
            await db.execute(text(stmt), {"t": tenant_id})
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


async def _status(tenant_id: uuid.UUID, token: str, is_active: bool):
    async with httpx.AsyncClient(app=app, base_url=BASE) as client:
        return await client.patch(
            f"/api/admin/tenants/{tenant_id}/status",
            headers={"Authorization": f"Bearer {token}"},
            json={"is_active": is_active},
        )


async def _is_active(tenant_id: uuid.UUID) -> bool:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                text("SELECT is_active FROM tenants WHERE id = :id"), {"id": tenant_id}
            )
        ).scalar_one()


async def _status_audits(tenant_id: uuid.UUID) -> list[tuple]:
    async with AsyncSessionLocal() as db:
        return list(
            (
                await db.execute(
                    text(
                        "SELECT actor_id, entity_type, entity_id, old_values, new_values "
                        "FROM audit_events WHERE tenant_id = :t "
                        "AND action = 'tenant.status_changed'"
                    ),
                    {"t": tenant_id},
                )
            ).all()
        )


class TestSuspend:
    async def test_admin_can_suspend_active_tenant(self):
        """1 + 2 + 3: suspend, before=true/after=false, DB becomes false."""
        tenant, admin = await _make_tenant("Suspend Me", active=True)
        try:
            resp = await _status(tenant.id, _token(admin), False)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["changed"] is True
            assert body["before"] == {"is_active": True}
            assert body["after"] == {"is_active": False}
            assert body["tenant_id"] == str(tenant.id)
            assert await _is_active(tenant.id) is False
        finally:
            await _drop(tenant.id)

    async def test_suspend_writes_audit_with_tenant_actor_and_values(self):
        """4 + 5 + 6 + 7 + 8: the audit event and every canonical field."""
        tenant, admin = await _make_tenant("Audit Suspend", active=True)
        try:
            assert (await _status(tenant.id, _token(admin), False)).status_code == 200
            rows = await _status_audits(tenant.id)
            assert len(rows) == 1
            actor_id, entity_type, entity_id, old_values, new_values = rows[0]
            assert entity_type == "tenant"
            assert str(entity_id) == str(tenant.id)
            assert str(actor_id) == str(admin.id)          # 6: real administrator
            assert old_values == {"is_active": True}        # 7
            assert new_values == {"is_active": False}       # 8
        finally:
            await _drop(tenant.id)


class TestReactivate:
    async def test_admin_can_reactivate_suspended_tenant(self):
        """9 + 10 + 11: reactivate, before=false/after=true, audited."""
        tenant, admin = await _make_tenant("Reactivate Me", active=False)
        try:
            resp = await _status(tenant.id, _token(admin), True)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["changed"] is True
            assert body["before"] == {"is_active": False}
            assert body["after"] == {"is_active": True}
            assert await _is_active(tenant.id) is True

            rows = await _status_audits(tenant.id)
            assert len(rows) == 1
            assert rows[0][3] == {"is_active": False}
            assert rows[0][4] == {"is_active": True}
        finally:
            await _drop(tenant.id)

    async def test_full_cycle_records_both_transitions(self):
        """Suspend then reactivate = exactly two audit events, in order."""
        tenant, admin = await _make_tenant("Cycle Me", active=True)
        try:
            token = _token(admin)
            assert (await _status(tenant.id, token, False)).status_code == 200
            assert (await _status(tenant.id, token, True)).status_code == 200
            rows = await _status_audits(tenant.id)
            assert len(rows) == 2
            assert rows[0][4] == {"is_active": False}
            assert rows[1][4] == {"is_active": True}
        finally:
            await _drop(tenant.id)


class TestNoop:
    async def test_active_to_active_is_noop_without_audit(self):
        """12 + 13."""
        tenant, admin = await _make_tenant("Already Active", active=True)
        try:
            resp = await _status(tenant.id, _token(admin), True)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["changed"] is False
            assert body["before"] == {"is_active": True}
            assert body["after"] == {"is_active": True}
            assert await _status_audits(tenant.id) == []
            assert await _is_active(tenant.id) is True
        finally:
            await _drop(tenant.id)

    async def test_inactive_to_inactive_is_noop_without_audit(self):
        """14 + 15."""
        tenant, admin = await _make_tenant("Already Suspended", active=False)
        try:
            resp = await _status(tenant.id, _token(admin), False)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["changed"] is False
            assert body["before"] == {"is_active": False}
            assert body["after"] == {"is_active": False}
            assert await _status_audits(tenant.id) == []
            assert await _is_active(tenant.id) is False
        finally:
            await _drop(tenant.id)


class TestAuthorizationAndIsolation:
    async def test_non_admin_receives_403(self):
        """16: standard 403, and nothing changes."""
        tenant, user = await _make_tenant("Not An Admin", active=True)
        try:
            resp = await _status(tenant.id, _token(user, superuser=False), False)
            assert resp.status_code == 403, resp.text
            assert await _is_active(tenant.id) is True
            assert await _status_audits(tenant.id) == []
        finally:
            await _drop(tenant.id)

    async def test_unknown_tenant_receives_404(self):
        """17."""
        tenant, admin = await _make_tenant("Unknown Target")
        try:
            resp = await _status(uuid.uuid4(), _token(admin), False)
            assert resp.status_code == 404, resp.text
        finally:
            await _drop(tenant.id)

    async def test_soft_deleted_tenant_receives_404(self):
        """18."""
        tenant, admin = await _make_tenant("Deleted Target")
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
            resp = await _status(tenant.id, _token(admin), False)
            assert resp.status_code == 404, resp.text
            assert await _status_audits(tenant.id) == []
        finally:
            await _drop(tenant.id)

    async def test_cross_tenant_mutation_is_impossible(self):
        """19: suspending A must not touch B."""
        tenant_a, admin = await _make_tenant("Suspension Target", active=True)
        tenant_b, _ = await _make_tenant("Bystander", active=True)
        try:
            resp = await _status(tenant_a.id, _token(admin), False)
            assert resp.status_code == 200, resp.text
            assert await _is_active(tenant_a.id) is False
            # B is untouched, and no audit event was attributed to it.
            assert await _is_active(tenant_b.id) is True
            assert await _status_audits(tenant_b.id) == []
        finally:
            await _drop(tenant_a.id)
            await _drop(tenant_b.id)

    async def test_unknown_field_rejected(self):
        """The payload is closed: a typo cannot silently smuggle state."""
        tenant, admin = await _make_tenant("Closed Payload", active=True)
        try:
            async with httpx.AsyncClient(app=app, base_url=BASE) as client:
                resp = await client.patch(
                    f"/api/admin/tenants/{tenant.id}/status",
                    headers={"Authorization": f"Bearer {_token(admin)}"},
                    json={"is_active": False, "is_deleted": True},
                )
            assert resp.status_code == 422, resp.text
            assert await _is_active(tenant.id) is True
        finally:
            await _drop(tenant.id)


class TestDataPreservation:
    async def test_suspension_preserves_all_tenant_data(self):
        """20 + 21 + 22 + 23 - and audit history is retained, not deleted."""
        tenant, admin = await _make_tenant("Preserve Me", active=True, seed=True)
        try:
            before = await _counts(tenant.id)
            # Preconditions: the seed really did create tenant-owned data.
            assert before["users"] == 1
            assert before["roles"] == 1
            assert before["tenant_custom_properties"] == 1
            assert before["discovery_scopes"] == 1

            assert (await _status(tenant.id, _token(admin), False)).status_code == 200
            after = await _counts(tenant.id)

            assert after["users"] == before["users"]                        # 20
            assert after["roles"] == before["roles"]                        # 21
            assert (
                after["tenant_custom_properties"] == before["tenant_custom_properties"]
            )                                                              # 22
            assert after["discovery_scopes"] == before["discovery_scopes"]   # 23
            # The suspend added exactly one audit row - nothing was removed.
            assert after["audit_events"] == before["audit_events"] + 1
        finally:
            await _drop(tenant.id)

    async def test_reactivation_preserves_all_tenant_data(self):
        """Reactivation restores is_active without recreating anything."""
        tenant, admin = await _make_tenant("Reactivate Keep", active=False, seed=True)
        try:
            before = await _counts(tenant.id)
            assert (await _status(tenant.id, _token(admin), True)).status_code == 200
            after = await _counts(tenant.id)
            assert await _is_active(tenant.id) is True
            assert after["users"] == before["users"]
            assert after["roles"] == before["roles"]
            assert after["tenant_custom_properties"] == before["tenant_custom_properties"]
            assert after["discovery_scopes"] == before["discovery_scopes"]
        finally:
            await _drop(tenant.id)

    async def test_audit_payload_carries_no_credentials(self):
        """The audit event holds lifecycle state only."""
        tenant, admin = await _make_tenant("No Secrets", active=True, seed=True)
        try:
            assert (await _status(tenant.id, _token(admin), False)).status_code == 200
            rows = await _status_audits(tenant.id)
            assert len(rows) == 1
            assert set({**rows[0][3], **rows[0][4]}) == {"is_active"}
            blob = f"{rows[0][3]}{rows[0][4]}".lower()
            for forbidden in ("password", "hash", "token", "secret", "api_key"):
                assert forbidden not in blob
        finally:
            await _drop(tenant.id)
