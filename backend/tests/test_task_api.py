"""
Task API endpoint tests — validates FastAPI routes for task lifecycle.

Uses JWT auth (same convention as test_incident_lifecycle.py).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.main import app

pytestmark = pytest.mark.asyncio

DEV_TENANT_ID = uuid.UUID("54dd0ca8-873f-4b8e-bb54-c4f45f64d0b3")


async def _create_agent_for_task(tenant_id: uuid.UUID) -> str:
    from app.core.db import AsyncSessionLocal
    from app.models.agent import Agent

    async with AsyncSessionLocal() as db:
        agent = Agent(
            tenant_id=tenant_id,
            agent_id=f"AGT-{uuid.uuid4().hex[:6].upper()}",
            name="Task API Agent",
            role="noc_engineer",
            department="noc",
            level="l1",
            is_active=True,
            current_workload=0,
            max_workload=5,
            skills=["networking"],
            supported_task_types=["alert_triage"],
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return str(agent.id)


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


async def _create_task(client, **overrides) -> dict:
    payload = {
        "title": "API Test Task",
        "task_type": "alert_triage",
        "priority": "P3",
    }
    payload.update(overrides)
    resp = await client.post(
        "/api/tasks",
        params={"tenant_id": str(DEV_TENANT_ID)},
        headers=_auth_headers(DEV_TENANT_ID),
        json=payload,
    )
    return resp


class TestTaskAPICreate:
    async def test_create_task_returns_201(self, client):
        resp = await _create_task(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "API Test Task"
        assert data["status"] == "created"

    async def test_create_task_generates_task_number(self, client):
        resp = await _create_task(client)
        assert resp.status_code == 201
        assert resp.json()["task_number"].startswith("TSK-")


class TestTaskAPIList:
    async def test_list_tasks_returns_200(self, client):
        resp = await client.get(
            "/api/tasks",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestTaskAPIGet:
    async def test_get_task_returns_200(self, client):
        create_resp = await _create_task(client, title="Get Task")
        task_id = create_resp.json()["id"]
        resp = await client.get(
            f"/api/tasks/{task_id}",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == task_id

    async def test_get_nonexistent_returns_404(self, client):
        resp = await client.get(
            f"/api/tasks/{uuid.uuid4()}",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 404


class TestTaskAPILifecycle:
    async def test_worker_execution_route_for_assigned_agent(self, client):
        create_resp = await _create_task(client, title="Worker Execution Task")
        assert create_resp.status_code == 201
        task_id = create_resp.json()["id"]
        agent_id = await _create_agent_for_task(DEV_TENANT_ID)

        assign_resp = await client.post(
            f"/api/tasks/{task_id}/assign",
            params={"tenant_id": str(DEV_TENANT_ID), "assigned_agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert assign_resp.status_code == 200

        accept_resp = await client.post(
            f"/api/tasks/{task_id}/accept",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert accept_resp.status_code == 200

        worker_resp = await client.post(
            f"/api/tasks/{task_id}/worker-execute",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
            json={
                "tool_name": "prtg_lookup",
                "execution_input": {"sensor": "root"},
                "allowed_tools": ["prtg_lookup"],
            },
        )
        assert worker_resp.status_code == 200
        payload = worker_resp.json()
        assert payload["status"] == "verifying"
        assert payload["execution_result"]["status"] == "completed"

    async def test_full_lifecycle(self, client):
        create_resp = await _create_task(client, title="Lifecycle Task")
        assert create_resp.status_code == 201
        task_id = create_resp.json()["id"]
        agent_id = await _create_agent_for_task(DEV_TENANT_ID)

        # Assign
        assign_resp = await client.post(
            f"/api/tasks/{task_id}/assign",
            params={"tenant_id": str(DEV_TENANT_ID), "assigned_agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert assign_resp.status_code == 200
        assert assign_resp.json()["status"] == "assigned"

        # Accept
        accept_resp = await client.post(
            f"/api/tasks/{task_id}/accept",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert accept_resp.status_code == 200
        assert accept_resp.json()["status"] == "accepted"

        # Start investigation
        inv_resp = await client.post(
            f"/api/tasks/{task_id}/investigate",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert inv_resp.status_code == 200
        assert inv_resp.json()["status"] == "investigating"

        # Start execution
        exec_resp = await client.post(
            f"/api/tasks/{task_id}/execute",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert exec_resp.status_code == 200
        assert exec_resp.json()["status"] == "executing"

        refreshed = await client.get(
            f"/api/tasks/{task_id}",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["status"] == "executing"

        # Submit execution
        submit_resp = await client.post(
            f"/api/tasks/{task_id}/submit-execution",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
            json={"execution_result": {"status": "completed"}},
        )
        assert submit_resp.status_code == 200
        assert submit_resp.json()["status"] == "verifying"

        # Verify
        verify_resp = await client.post(
            f"/api/tasks/{task_id}/verify",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": agent_id},
            headers=_auth_headers(DEV_TENANT_ID),
            json={"verification_result": {"passed": True}},
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["status"] == "verified"
        assert verify_resp.json()["verified"] is True

        # Record
        record_resp = await client.post(
            f"/api/tasks/{task_id}/record",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert record_resp.status_code == 200
        assert record_resp.json()["status"] == "recorded"

    async def test_invalid_transition_returns_409(self, client):
        create_resp = await _create_task(client)
        task_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/tasks/{task_id}/verify",
            params={"tenant_id": str(DEV_TENANT_ID), "agent_id": str(uuid.uuid4())},
            headers=_auth_headers(DEV_TENANT_ID),
            json={"verification_result": {"passed": True}},
        )
        assert resp.status_code == 409

    async def test_cancel_task(self, client):
        create_resp = await _create_task(client, title="Cancel Task")
        task_id = create_resp.json()["id"]
        resp = await client.post(
            f"/api/tasks/{task_id}/cancel",
            params={"tenant_id": str(DEV_TENANT_ID)},
            headers=_auth_headers(DEV_TENANT_ID),
            json={"reason": "no longer needed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


    async def test_list_tasks_with_status_filter(self, client):
        resp = await client.get(
            "/api/tasks",
            params={"tenant_id": str(DEV_TENANT_ID), "status": "created"},
            headers=_auth_headers(DEV_TENANT_ID),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] == "created" for t in data)
