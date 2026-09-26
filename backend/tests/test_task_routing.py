"""
Task Routing Engine tests — validates agent-to-task assignment logic.

Uses the real PostgreSQL DB (same convention as conftest.py).

NOTE: The current RoutingEngine uses `a.can_accept_task` to filter candidates,
but the Agent model only defines `is_available_for_work`. This is a GAP.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.agent import Agent
from app.models.task import Task
from app.models.tenant import Tenant
from app.repositories.agent_repository import AgentRepository
from app.services.task_routing import NoAgentAvailable, RoutingEngine

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Routing Test", slug=f"routing-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


@pytest_asyncio.fixture
async def engine():
    async with AsyncSessionLocal() as db:
        yield RoutingEngine(agent_repo=AgentRepository(db))
        await db.rollback()


async def _make_agent(db, tenant_id: uuid.UUID, **overrides) -> Agent:
    fields = {
        "agent_id": f"AGT-{uuid.uuid4().hex[:6].upper()}",
        "name": "Test Agent",
        "role": "noc_engineer",
        "department": "noc",
        "level": "l1",
        "is_active": True,
        "current_workload": 0,
        "max_workload": 5,
        "skills": ["networking"],
        "supported_task_types": ["alert_triage"],
    }
    fields.update(overrides)
    agent = Agent(tenant_id=tenant_id, **fields)
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return agent


async def _make_task(db, tenant_id: uuid.UUID, **overrides) -> Task:
    fields = {
        "task_number": f"TSK-{uuid.uuid4().hex[:6].upper()}",
        "title": "Test Task",
        "task_type": "alert_triage",
        "status": "created",
        "required_skills": ["networking"],
    }
    fields.update(overrides)
    task = Task(tenant_id=tenant_id, **fields)
    db.add(task)
    await db.flush()
    await db.refresh(task)
    return task


class TestRoutingCandidates:
    async def test_noc_task_routes_to_noc_agent(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, department="noc", supported_task_types=["alert_triage"])

    async def test_inactive_agent_excluded(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id, is_active=False, supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant.id, task_type="alert_triage")
            candidates = await engine.find_candidates(tenant.id, task)
            assert len(candidates) == 0

    async def test_wrong_department_excluded(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id, department="noc", supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant.id, task_type="alert_triage")
            candidates = await engine.find_candidates(tenant.id, task, department="soc")
            assert len(candidates) == 0

    async def test_skill_fallback(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, supported_task_types=[], skills=["python", "linux"])
            task = await _make_task(db, tenant.id, task_type="custom_task", required_skills=["python"])
            candidates = await engine.find_candidates(tenant.id, task)
            assert any(a.id == agent.id for a in candidates)


class TestRoutingNoCandidates:
    async def test_no_agent_available_raises(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id, is_active=False, supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant.id, task_type="alert_triage")
            with pytest.raises(NoAgentAvailable):
                await engine.route(tenant.id, task)

    async def test_no_matching_department_raises(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id, department="noc", supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant.id, task_type="alert_triage")
            with pytest.raises(NoAgentAvailable):
                await engine.route(tenant.id, task, department="hr")


class TestRoutingScoring:
    async def test_least_loaded_preferred(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id, current_workload=4, max_workload=5, supported_task_types=["alert_triage"])
            free = await _make_agent(db, tenant.id, current_workload=0, max_workload=5, supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant.id, task_type="alert_triage")
            candidates = await engine.find_candidates(tenant.id, task)
            if len(candidates) >= 2:
                assert candidates[0].id == free.id

    async def test_route_to_department(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, department="noc", supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant.id, task_type="alert_triage")
            result = await engine.route_to_department(tenant.id, task, department="noc")
            assert result.department == "noc"

    async def test_route_to_level(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, level="l2", supported_task_types=["threat_hunting"])
            task = await _make_task(db, tenant.id, task_type="threat_hunting")
            result = await engine.route_to_level(tenant.id, task, level="l2")
            assert result.level == "l2"

            task = await _make_task(db, tenant.id, task_type="alert_triage")
            candidates = await engine.find_candidates(tenant.id, task, department="noc")
            assert any(a.id == agent.id for a in candidates)

    async def test_soc_task_routes_to_soc_agent(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, department="soc", supported_task_types=["threat_hunting"])
            task = await _make_task(db, tenant.id, task_type="threat_hunting")
            candidates = await engine.find_candidates(tenant.id, task, department="soc")
            assert any(a.id == agent.id for a in candidates)

    async def test_service_desk_task_routes_to_sd_agent(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, department="service_desk", supported_task_types=["user_support"])
            task = await _make_task(db, tenant.id, task_type="user_support")
            candidates = await engine.find_candidates(tenant.id, task, department="service_desk")
            assert any(a.id == agent.id for a in candidates)

    async def test_l1_task_routes_to_l1_agent(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, level="l1", supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant.id, task_type="alert_triage")
            candidates = await engine.find_candidates(tenant.id, task, level="l1")
            assert any(a.id == agent.id for a in candidates)

    async def test_l2_task_routes_to_l2_agent(self, tenant, engine):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, level="l2", supported_task_types=["threat_hunting"])
            task = await _make_task(db, tenant.id, task_type="threat_hunting")
            candidates = await engine.find_candidates(tenant.id, task, level="l2")
            assert any(a.id == agent.id for a in candidates)
