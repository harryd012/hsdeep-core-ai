"""
Agent API endpoint tests — validates FastAPI routes for agent management.

Uses JWT auth (same convention as test_incident_lifecycle.py).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.main import app

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("dev_tenant")]

DEV_TENANT_ID = uuid.UUID("54dd0ca8-873f-4b8e-bb54-c4f45f64d0b3")


def _auth_headers(tenant_id: uuid.UUID) -> dict:
    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        email="test@hsdeep.local",
        is_superuser=True,
        permissions=frozenset(),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


class TestAgentAPIList:
    async def test_list_agents_returns_200(self, client):
        resp = await client.get(
            "/api/agents",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_list_agents_with_department_filter(self, client):
        resp = await client.get(
            "/api/agents",
            params={"tenant_id": str(DEV_TENANT_ID), "department": "noc"},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(a.get("department") == "noc" for a in data)

    async def test_list_available_agents(self, client):
        resp = await client.get(
            "/api/agents/available",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestAgentAPICreate:
    async def test_create_agent_returns_201(self, client):
        resp = await client.post(
            "/api/agents",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
            json={
                "agent_id": f"API-{uuid.uuid4().hex[:6].upper()}",
                "name": "API Test Agent",
                "role": "noc_engineer",
                "department": "noc",
                "level": "l1",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "API Test Agent"
        assert data["department"] == "noc"

    async def test_create_agent_returns_uuid(self, client):
        resp = await client.post(
            "/api/agents",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
            json={
                "agent_id": f"UUID-{uuid.uuid4().hex[:6].upper()}",
                "name": "UUID Test",
                "role": "noc_engineer",
                "department": "noc",
                "level": "l1",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data


class TestAgentAPIGet:
    async def test_get_agent_returns_200(self, client):
        create_resp = await client.post(
            "/api/agents",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
            json={
                "agent_id": f"GET-{uuid.uuid4().hex[:6].upper()}",
                "name": "Get Test",
                "role": "noc_engineer",
                "department": "noc",
                "level": "l1",
            },
        )
        agent_id = create_resp.json()["id"]
        resp = await client.get(
            f"/api/agents/{agent_id}",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == agent_id

    async def test_get_nonexistent_returns_404(self, client):
        resp = await client.get(
            f"/api/agents/{uuid.uuid4()}",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 404


class TestAgentAPIUpdate:
    async def test_update_agent_returns_200(self, client):
        create_resp = await client.post(
            "/api/agents",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
            json={
                "agent_id": f"UPD-{uuid.uuid4().hex[:6].upper()}",
                "name": "Update Me",
                "role": "noc_engineer",
                "department": "noc",
                "level": "l1",
            },
        )
        agent_id = create_resp.json()["id"]
        resp = await client.patch(
            f"/api/agents/{agent_id}",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
            json={"name": "Updated Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"


class TestAgentAPIAvailability:
    async def test_set_availability(self, client):
        create_resp = await client.post(
            "/api/agents",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
            json={
                "agent_id": f"AVL-{uuid.uuid4().hex[:6].upper()}",
                "name": "Availability Test",
                "role": "noc_engineer",
                "department": "noc",
                "level": "l1",
            },
        )
        agent_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/agents/{agent_id}/availability",
            params={"tenant_id": str(DEV_TENANT_ID), "availability": "busy"},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        assert resp.json()["availability"] == "busy"
