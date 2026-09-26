"""
Agent Repository tests — validates the data access layer for Agent model.

Uses the real PostgreSQL DB (same convention as conftest.py).
Tenant-scoped isolation: each test creates its own tenant, cleaned up at teardown.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.repositories.agent_repository import AgentRepository

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant():
    """Create a test tenant, cleaned up at teardown."""
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Agent Repo Test", slug=f"agent-repo-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))


async def _make_agent(repo: AgentRepository, tenant_id: uuid.UUID, **overrides) -> Agent:
    fields = {
        "agent_id": f"AGT-{uuid.uuid4().hex[:6].upper()}",
        "name": "Test Agent",
        "role": "noc_engineer",
        "department": "noc",
        "level": "l1",
    }
    fields.update(overrides)
    return await repo.create(tenant_id=tenant_id, **fields)


class TestAgentRepositoryCreate:
    async def test_create_agent(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id, name="NOC Agent", department="noc")
        assert agent.id is not None
        assert agent.tenant_id == tenant.id
        assert agent.name == "NOC Agent"
        assert agent.status == "active"
        assert agent.is_active is True

    async def test_create_generates_uuid(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id)
        assert isinstance(agent.id, uuid.UUID)

    async def test_create_with_all_fields(self, tenant, repo):
        agent = await _make_agent(
            repo, tenant_id=tenant.id, name="Full Agent", role="soc_analyst",
            department="soc", level="l2", skills=["siem", "threat_intel"],
            capabilities=["investigation", "response"],
            supported_task_types=["alert_triage", "incident_response"], max_workload=10,
        )
        assert agent.skills == ["siem", "threat_intel"]
        assert agent.max_workload == 10


class TestAgentRepositoryGet:
    async def test_get_by_id(self, tenant, repo):
        created = await _make_agent(repo, tenant.id, name="Fetch Me")
        fetched = await repo.get(created.id, tenant.id)
        assert fetched is not None
        assert fetched.id == created.id

    async def test_get_returns_none_for_wrong_tenant(self, tenant, repo):
        created = await _make_agent(repo, tenant.id)
        fetched = await repo.get(created.id, uuid.uuid4())
        assert fetched is None

    async def test_get_by_agent_id_string(self, tenant, repo):
        created = await _make_agent(repo, tenant.id, agent_id="CTO-001")
        fetched = await repo.get_by_agent_id("CTO-001", tenant.id)
        assert fetched is not None



class TestAgentRepositoryList:
    async def test_list_all(self, tenant, repo):
        for i in range(3):
            await _make_agent(repo, tenant.id, name=f"Agent {i}")
        agents = await repo.list(tenant.id)
        assert len(agents) >= 3

    async def test_list_filter_by_department(self, tenant, repo):
        await _make_agent(repo, tenant.id, department="noc", name="NOC")
        await _make_agent(repo, tenant.id, department="soc", name="SOC")
        agents = await repo.list(tenant.id, department="noc")
        assert all(a.department == "noc" for a in agents)

    async def test_list_filter_by_level(self, tenant, repo):
        await _make_agent(repo, tenant.id, level="l1")
        await _make_agent(repo, tenant.id, level="l2")
        agents = await repo.list(tenant.id, level="l2")
        assert all(a.level == "l2" for a in agents)

    async def test_list_filter_by_status(self, tenant, repo):
        await _make_agent(repo, tenant.id, status="active")
        await _make_agent(repo, tenant.id, status="suspended")
        agents = await repo.list(tenant.id, status="suspended")
        assert all(a.status == "suspended" for a in agents)

    async def test_list_filter_by_active(self, tenant, repo):
        await _make_agent(repo, tenant.id, is_active=True)
        await _make_agent(repo, tenant.id, is_active=False)
        active_agents = await repo.list(tenant.id, is_active=True)
        assert all(a.is_active for a in active_agents)

    async def test_list_pagination(self, tenant, repo):
        for i in range(5):
            await _make_agent(repo, tenant.id, name=f"Agent {i}")
        page1 = await repo.list(tenant.id, limit=2, offset=0)
        page2 = await repo.list(tenant.id, limit=2, offset=2)
        assert len(page1) == 2
        ids1 = {a.id for a in page1}
        ids2 = {a.id for a in page2}
        assert ids1.isdisjoint(ids2)

    async def test_get_excludes_soft_deleted(self, tenant, repo):
        created = await _make_agent(repo, tenant.id)
        created.is_deleted = True
        await repo.db.flush()
        fetched = await repo.get(created.id, tenant.id)


class TestAgentRepositoryUpdate:
    async def test_update_fields(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id, name="Old Name")
        updated = await repo.update(agent, name="New Name")
        assert updated.name == "New Name"

    async def test_update_ignores_invalid_fields(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id)
        updated = await repo.update(agent, name="Valid", nonexistent_field="ignored")
        assert updated.name == "Valid"


class TestAgentRepositoryWorkload:
    async def test_increment_workload(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id, current_workload=0)
        await repo.increment_workload(agent.id, tenant.id)
        fetched = await repo.get(agent.id, tenant.id)
        assert fetched.current_workload == 1

    async def test_decrement_workload(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id, current_workload=2)
        await repo.decrement_workload(agent.id, tenant.id)
        fetched = await repo.get(agent.id, tenant.id)
        assert fetched.current_workload == 1

    async def test_decrement_never_negative(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id, current_workload=0)
        await repo.decrement_workload(agent.id, tenant.id)
        fetched = await repo.get(agent.id, tenant.id)
        assert fetched.current_workload == 0


class TestAgentRepositoryPerformance:
    async def test_record_success(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id)
        await repo.record_task_completion(agent.id, tenant.id, success=True, duration_minutes=30.0)
        fetched = await repo.get(agent.id, tenant.id)
        assert fetched.tasks_completed == 1
        assert fetched.tasks_failed == 0

    async def test_record_failure(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id)
        await repo.record_task_completion(agent.id, tenant.id, success=False)
        fetched = await repo.get(agent.id, tenant.id)
        assert fetched.tasks_failed == 1

    async def test_avg_resolution_running(self, tenant, repo):
        agent = await _make_agent(repo, tenant.id)
        await repo.record_task_completion(agent.id, tenant.id, success=True, duration_minutes=20.0)
        await repo.record_task_completion(agent.id, tenant.id, success=True, duration_minutes=40.0)
        fetched = await repo.get(agent.id, tenant.id)
        assert fetched.avg_resolution_minutes == 30.0


class TestAgentRepositoryAvailable:
    async def test_list_available_filters_active(self, tenant, repo):
        await _make_agent(repo, tenant.id, is_active=True, current_workload=0, max_workload=5)
        await _make_agent(repo, tenant.id, is_active=False, current_workload=0, max_workload=5)
        available = await repo.list_available(tenant.id)
        assert all(a.is_active for a in available)

    async def test_list_available_filters_workload(self, tenant, repo):
        await _make_agent(repo, tenant.id, current_workload=0, max_workload=5, name="Free Agent")
        await _make_agent(repo, tenant.id, current_workload=5, max_workload=5, name="Full Agent")
        available = await repo.list_available(tenant.id)
        assert all(a.name != "Full Agent" for a in available)

    async def test_list_by_task_type(self, tenant, repo):
        await _make_agent(repo, tenant.id, supported_task_types=["alert_triage"])
        await _make_agent(repo, tenant.id, supported_task_types=["patch_management"])
        candidates = await repo.list_by_task_type(tenant.id, "alert_triage")
        assert len(candidates) >= 1

    async def test_list_by_skills_overlap(self, tenant, repo):
        await _make_agent(repo, tenant.id, skills=["python", "linux"])
        await _make_agent(repo, tenant.id, skills=["windows", "active_directory"])
        candidates = await repo.list_by_skills(tenant.id, ["python"])
        assert len(candidates) >= 1


@pytest_asyncio.fixture
async def repo():
    """AgentRepository with its own session."""
    async with AsyncSessionLocal() as db:
        yield AgentRepository(db)
        await db.rollback()
