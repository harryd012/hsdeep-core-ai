"""
Agent Service tests — validates the agent management business logic.

Uses the real PostgreSQL DB (same convention as conftest.py).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.tenant import Tenant
from app.repositories.agent_repository import AgentRepository
from app.services.agent_service import AgentNotFound, AgentService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Agent Svc Test", slug=f"agent-svc-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


@pytest_asyncio.fixture
async def service():
    async with AsyncSessionLocal() as db:
        yield AgentService(agent_repo=AgentRepository(db))
        await db.rollback()


class TestAgentServiceCreate:
    async def test_create_agent(self, tenant, service):
        agent = await service.create_agent(
            tenant.id, agent_id="NOC-001", name="NOC Engineer",
            role="noc_engineer", department="noc", level="l1",
        )
        assert agent.id is not None
        assert agent.name == "NOC Engineer"
        assert agent.department == "noc"

    async def test_create_with_skills(self, tenant, service):
        agent = await service.create_agent(
            tenant.id, agent_id="SOC-001", name="SOC Analyst",
            role="soc_analyst", department="soc", level="l2",
            skills=["siem", "threat_intel"], max_workload=8,
        )
        assert agent.skills == ["siem", "threat_intel"]
        assert agent.max_workload == 8


class TestAgentServiceGet:
    async def test_get_existing(self, tenant, service):
        created = await service.create_agent(
            tenant.id, agent_id="GET-001", name="Get Me",
            role="noc_engineer", department="noc", level="l1",
        )
        fetched = await service.get(created.id, tenant.id)
        assert fetched.id == created.id

    async def test_get_not_found_raises(self, tenant, service):
        with pytest.raises(AgentNotFound):
            await service.get(uuid.uuid4(), tenant.id)

    async def test_get_by_agent_id(self, tenant, service):
        await service.create_agent(
            tenant.id, agent_id="CTO-001", name="CTO",
            role="cto", department="engineering", level="cto",
        )
        fetched = await service.get_by_agent_id("CTO-001", tenant.id)
        assert fetched.agent_id == "CTO-001"


class TestAgentServiceUpdate:
    async def test_update_agent(self, tenant, service):
        agent = await service.create_agent(
            tenant.id, agent_id="UPD-001", name="Old Name",
            role="noc_engineer", department="noc", level="l1",
        )
        updated = await service.update_agent(agent.id, tenant.id, name="New Name")
        assert updated.name == "New Name"


class TestAgentServiceAvailability:
    async def test_set_availability(self, tenant, service):
        agent = await service.create_agent(
            tenant.id, agent_id="AVL-001", name="Available",
            role="noc_engineer", department="noc", level="l1",
        )
        updated = await service.set_availability(agent.id, tenant.id, "busy")
        assert updated.availability == "busy"


class TestAgentServiceTaskTracking:
    async def test_assign_task_increments_workload(self, tenant, service):
        agent = await service.create_agent(
            tenant.id, agent_id="ASG-001", name="Assign Test",
            role="noc_engineer", department="noc", level="l1",
            current_workload=0, max_workload=5,
        )
        await service.assign_task(agent.id, tenant.id)
        fetched = await service.get(agent.id, tenant.id)
        assert fetched.current_workload == 1

    async def test_complete_task_updates_performance(self, tenant, service):
        agent = await service.create_agent(
            tenant.id, agent_id="CMP-001", name="Complete Test",
            role="noc_engineer", department="noc", level="l1",
            current_workload=1,
        )
        await service.complete_task(agent.id, tenant.id, success=True, duration_minutes=15.0)
        fetched = await service.get(agent.id, tenant.id)
        assert fetched.tasks_completed == 1
        assert fetched.current_workload == 0


class TestAgentServiceOrgTree:
    async def test_get_organization_tree(self, tenant, service):
        cto = await service.create_agent(
            tenant.id, agent_id="CTO-001", name="CTO",
            role="cto", department="engineering", level="cto",
        )
        await service.create_agent(
            tenant.id, agent_id="MGR-001", name="NOC Manager",
            role="noc_manager", department="noc", level="manager",
            manager_id=cto.id,
        )
        tree = await service.get_organization_tree(tenant.id)
        assert len(tree) >= 1
        # CTO should be root
        root_ids = {node["agent_id"] for node in tree}
        assert "CTO-001" in root_ids

    async def test_get_cto(self, tenant, service):
        await service.create_agent(
            tenant.id, agent_id="CTO-001", name="CTO",
            role="cto", department="engineering", level="cto",
        )
        cto = await service.get_cto(tenant.id)
        assert cto is not None
        assert cto.role == "cto"

    async def test_get_manager_for_department(self, tenant, service):
        await service.create_agent(
            tenant.id, agent_id="MGR-NOC", name="NOC Manager",
            role="noc_manager", department="noc", level="manager",
        )
        mgr = await service.get_manager(tenant.id, "noc")
        assert mgr is not None
        assert mgr.department == "noc"
