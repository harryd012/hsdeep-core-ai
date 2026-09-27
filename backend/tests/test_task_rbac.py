"""RBAC enforcement on the governed task endpoints.

The task lifecycle routes were authenticated and tenant scoped but carried no
permission requirement, so any authenticated principal in the tenant could
approve, execute, assign or cancel work - including the decisions that release
protected infrastructure actions. They now use the SAME mechanism the rest of
the API already uses (`require_permission`, with the `tickets.write` /
`tickets.execute` codes that the neighbouring assignment and ticket routes use),
rather than a new authorisation framework.

These tests are HTTP level on purpose: they prove the dependency is actually
attached to the route, which a unit test of the dependency factory cannot show.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.auth import create_access_token
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.tenant import Tenant

pytestmark = pytest.mark.asyncio


def _token(
    tenant_id: uuid.UUID,
    *,
    is_superuser: bool,
    permissions: tuple[str, ...] = (),
) -> dict:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="rbac@hsdeep.local",
        is_superuser=is_superuser,
        permissions=frozenset(permissions),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="RBAC Tenant", slug=f"rbac-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _create_task(client, tenant_id: uuid.UUID) -> str:
    """Created as a superuser so the fixture is about the route under test."""
    resp = await client.post(
        "/api/tasks",
        headers=_token(tenant_id, is_superuser=True),
        json={"title": "RBAC Task", "task_type": "alert_triage"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestTaskRoutePermissions:
    async def test_governed_endpoint_requires_authentication(self, client, tenant):
        resp = await client.post(f"/api/tasks/{uuid.uuid4()}/cancel")
        assert resp.status_code == 401

    async def test_cancel_is_refused_without_tickets_write(self, client, tenant):
        task_id = await _create_task(client, tenant.id)
        resp = await client.post(
            f"/api/tasks/{task_id}/cancel",
            headers=_token(tenant.id, is_superuser=False, permissions=("devices.read",)),
        )
        assert resp.status_code == 403
        assert "tickets.write" in resp.json()["detail"]

    async def test_cancel_succeeds_with_tickets_write(self, client, tenant):
        task_id = await _create_task(client, tenant.id)
        resp = await client.post(
            f"/api/tasks/{task_id}/cancel",
            headers=_token(tenant.id, is_superuser=False, permissions=("tickets.write",)),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    async def test_approval_decisions_need_the_execute_authority_not_just_write(
        self, client, tenant
    ):
        """Write access alone must not be able to release protected execution."""
        task_id = await _create_task(client, tenant.id)
        resp = await client.post(
            f"/api/tasks/{task_id}/approve",
            headers=_token(tenant.id, is_superuser=False, permissions=("tickets.write",)),
        )
        assert resp.status_code == 403
        assert "tickets.execute" in resp.json()["detail"]

    async def test_superuser_bypass_is_preserved(self, client, tenant):
        """Existing behaviour: a superuser needs no explicit grant."""
        task_id = await _create_task(client, tenant.id)
        resp = await client.post(
            f"/api/tasks/{task_id}/cancel",
            headers=_token(tenant.id, is_superuser=True, permissions=()),
        )
        assert resp.status_code == 200

    async def test_foreign_tenant_cannot_cancel_even_with_the_permission(
        self, client, tenant
    ):
        """RBAC does not replace tenancy: the permission is not a skeleton key."""
        task_id = await _create_task(client, tenant.id)
        other_tenant = uuid.uuid4()
        resp = await client.post(
            f"/api/tasks/{task_id}/cancel",
            headers=_token(other_tenant, is_superuser=True),
        )
        assert resp.status_code == 404
