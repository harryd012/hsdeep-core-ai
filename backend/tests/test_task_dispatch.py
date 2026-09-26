"""Task dispatch scheduler-job tests.

Regression suite for the defect where nothing ever moved a DB-backed task out
of ``created``: the scheduler only ran monitoring collection, so queued tasks
accumulated forever even though the routing engine and the task state machine
were fully implemented.

Every fixture row is COMMITTED on purpose. ``dispatch_queued_tasks()`` opens
its own session (as the live scheduler job does), so rows merely flushed in the
test session would be invisible to it.
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
from app.repositories.task_repository import TaskRepository
from app.scheduler.task_dispatch import dispatch_queued_tasks

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant():
    """A real committed Tenant, removed (cascades) at teardown."""
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Dispatch Test", slug=f"dispatch-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


async def _make_agent(db, tenant_id: uuid.UUID, **overrides) -> Agent:
    """A real, eligible agent (every predicate of is_available_for_work holds)."""
    fields = {
        "agent_id": f"AGT-{uuid.uuid4().hex[:6].upper()}",
        "name": "Dispatch Test Agent",
        "role": "noc_engineer",
        "department": "noc",
        "level": "l1",
        "is_active": True,
        "status": "active",
        "availability": "available",
        "current_workload": 0,
        "max_workload": 5,
        "skills": ["networking"],
        "supported_task_types": ["alert_triage"],
    }
    fields.update(overrides)
    agent = Agent(tenant_id=tenant_id, **fields)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


async def _make_task(db, tenant_id: uuid.UUID, **overrides) -> Task:
    fields = {
        "task_number": f"TSK-{uuid.uuid4().hex[:6].upper()}",
        "title": "Dispatch Test Task",
        "task_type": "alert_triage",
        "status": "created",
        "priority": "P3",
        "required_skills": [],
        "required_capabilities": [],
    }
    fields.update(overrides)
    task = Task(tenant_id=tenant_id, **fields)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


class TestDispatchQueuedTasks:
    """The discovered failure: queued tasks were never selected or assigned."""

    async def test_queued_task_leaves_created_and_is_assigned(self, tenant):
        """A real `created` task must transition to `assigned` with a real agent."""
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(db, tenant.id)
            task_id, agent_id = task.id, agent.id

        await dispatch_queued_tasks()

        async with AsyncSessionLocal() as db:
            after = await TaskRepository(db).get(task_id, tenant.id)
            assert after is not None
            assert after.status == "assigned", (
                "queued task was not dispatched; it is still stuck in 'created'"
            )
            assert after.assigned_agent_id == agent_id
            # Real persistence, not an in-memory illusion.
            assert after.assigned_at is not None
            assert after.updated_at > after.created_at

    async def test_dispatch_records_real_agent_workload(self, tenant):
        """Assignment must move the agent's real workload counter."""
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, current_workload=0, max_workload=5)
            await _make_task(db, tenant.id)
            agent_id = agent.id

        await dispatch_queued_tasks()

        async with AsyncSessionLocal() as db:
            refreshed = await AgentRepository(db).get(agent_id, tenant.id)
            assert refreshed.current_workload == 1

    async def test_task_with_no_eligible_agent_stays_queued(self, tenant):
        """No agent must mean 'still queued' - never a fabricated later state."""
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id)
            task_id = task.id

        await dispatch_queued_tasks()

        async with AsyncSessionLocal() as db:
            after = await TaskRepository(db).get(task_id, tenant.id)
            assert after.status == "created"
            assert after.assigned_agent_id is None
            # No fake execution, no silent failure.
            assert after.failure_reason is None
            assert after.completed_at is None

    async def test_agent_at_capacity_is_not_selected(self, tenant):
        """is_available_for_work requires current_workload < max_workload."""
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id, current_workload=5, max_workload=5)
            task = await _make_task(db, tenant.id)
            task_id = task.id

        await dispatch_queued_tasks()

        async with AsyncSessionLocal() as db:
            after = await TaskRepository(db).get(task_id, tenant.id)
            assert after.status == "created"
            assert after.assigned_agent_id is None

    async def test_terminal_tasks_are_never_redispatched(self, tenant):
        """Dispatch only serves `created`; it must not touch finished work."""
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id)
            done = await _make_task(db, tenant.id, status="recorded")
            done_id = done.id

        await dispatch_queued_tasks()

        async with AsyncSessionLocal() as db:
            after = await TaskRepository(db).get(done_id, tenant.id)
            assert after.status == "recorded"
            assert after.assigned_agent_id is None

    async def test_dispatch_is_idempotent_for_already_assigned(self, tenant):
        """A second pass must not reassign or corrupt an assigned task."""
        async with AsyncSessionLocal() as db:
            await _make_agent(db, tenant.id)
            task = await _make_task(db, tenant.id)
            task_id = task.id

        await dispatch_queued_tasks()
        async with AsyncSessionLocal() as db:
            first = await TaskRepository(db).get(task_id, tenant.id)
            first_agent, first_at = first.assigned_agent_id, first.assigned_at

        await dispatch_queued_tasks()
        async with AsyncSessionLocal() as db:
            second = await TaskRepository(db).get(task_id, tenant.id)
            assert second.status == "assigned"
            assert second.assigned_agent_id == first_agent
            assert second.assigned_at == first_at
