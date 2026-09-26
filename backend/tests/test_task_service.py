"""
Task Service tests — validates the task lifecycle state machine and business logic.

Uses the real PostgreSQL DB (same convention as conftest.py).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.repositories.task_repository import TaskRepository
from app.services.task_service import (
    AgentTaskPermissionError,
    TaskNotFound,
    TaskService,
    TaskTransitionNotAllowed,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Task Svc Test", slug=f"task-svc-{uuid.uuid4().hex[:8]}")
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
        yield TaskService(task_repo=TaskRepository(db))
        await db.rollback()


@pytest_asyncio.fixture
async def agent(tenant):
    """Create a real agent for tests that need a valid agent_id."""
    async with AsyncSessionLocal() as db:
        a = Agent(
            tenant_id=tenant.id,
            agent_id=f"AGT-{uuid.uuid4().hex[:6].upper()}",
            name="Test Agent",
            role="noc_engineer",
            department="noc",
            level="l1",
            is_active=True,
            current_workload=0,
            max_workload=5,
            skills=["networking"],
            supported_task_types=["alert_triage"],
        )
        db.add(a)
        await db.commit()
        await db.refresh(a)
        agent_id = a.id
    yield agent_id
    # NO explicit delete here. agents.tenant_id is ondelete=CASCADE, so the
    # `tenant` fixture teardown (which runs LAST, after the `service`
    # fixture has rolled back) removes this row via the cascade. An explicit
    # delete in THIS teardown would run BEFORE the service session's
    # rollback and deadlock: the service transaction holds uncommitted task
    # rows referencing this agent, so DELETE FROM agents waits on it forever
    # (the hang that surfaced once the event-loop fix let tests reach
    # teardown again).


class TestTaskServiceCreate:
    async def test_create_task(self, tenant, service):
        task = await service.create_task(tenant.id, title="New Task", task_type="alert_triage")
        assert task.id is not None
        assert task.status == "created"
        assert task.task_number.startswith("TSK-")

    async def test_create_generates_audit_entry(self, tenant, service):
        task = await service.create_task(tenant.id, title="Audited Task", task_type="alert_triage")
        assert len(task.audit_log) >= 1
        assert task.audit_log[0]["action"] == "created"

    async def test_create_with_priority(self, tenant, service):
        task = await service.create_task(tenant.id, title="P1 Task", task_type="alert_triage", priority="P1")
        assert task.priority == "P1"


class TestTaskServiceGet:
    async def test_get_existing(self, tenant, service):
        created = await service.create_task(tenant.id, title="Get Me", task_type="alert_triage")
        fetched = await service.get(created.id, tenant.id)
        assert fetched.id == created.id

    async def test_get_not_found_raises(self, tenant, service):
        with pytest.raises(TaskNotFound):
            await service.get(uuid.uuid4(), tenant.id)

    async def test_get_by_task_number(self, tenant, service):
        created = await service.create_task(tenant.id, title="By Number", task_type="alert_triage")


class TestTaskServiceTransitions:
    async def test_created_to_assigned(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Assign Me", task_type="alert_triage")
        assigned = await service.assign_task(task.id, tenant.id, agent)
        assert assigned.status == "assigned"
        assert assigned.assigned_agent_id == agent

    async def test_assigned_to_accepted(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Accept Me", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        accepted = await service.accept_task(task.id, tenant.id, agent)
        assert accepted.status == "accepted"

    async def test_accept_requires_assigned_agent(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Wrong Agent", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        with pytest.raises(AgentTaskPermissionError):
            await service.accept_task(task.id, tenant.id, uuid.uuid4())

    async def test_accepted_to_investigating(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Investigate", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        await service.accept_task(task.id, tenant.id, agent)
        investigating = await service.start_investigation(task.id, tenant.id, agent)
        assert investigating.status == "investigating"

    async def test_investigating_to_executing(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Execute", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        await service.accept_task(task.id, tenant.id, agent)
        await service.start_investigation(task.id, tenant.id, agent)
        executing = await service.start_execution(task.id, tenant.id, agent)
        assert executing.status == "executing"

    async def test_executing_to_verifying(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Verify", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        await service.accept_task(task.id, tenant.id, agent)
        await service.start_investigation(task.id, tenant.id, agent)
        await service.start_execution(task.id, tenant.id, agent)
        verifying = await service.submit_execution(task.id, tenant.id, agent, execution_result={"status": "done"})
        assert verifying.status == "verifying"

    async def test_verifying_to_verified(self, tenant, service, agent):
        created = await service.create_task(tenant.id, title="Complete", task_type="alert_triage")
        await service.assign_task(created.id, tenant.id, agent)
        await service.accept_task(created.id, tenant.id, agent)
        await service.start_investigation(created.id, tenant.id, agent)
        await service.start_execution(created.id, tenant.id, agent)
        await service.submit_execution(created.id, tenant.id, agent, execution_result={"status": "done"})
        verified = await service.verify_task(created.id, tenant.id, agent, verification_result={"passed": True})
        assert verified.status == "verified"
        assert verified.verified is True

        fetched = await service.get_by_task_number(created.task_number, tenant.id)
        assert fetched.id == created.id

    async def test_verified_to_recorded(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Record", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        await service.accept_task(task.id, tenant.id, agent)
        await service.start_investigation(task.id, tenant.id, agent)
        await service.start_execution(task.id, tenant.id, agent)
        await service.submit_execution(task.id, tenant.id, agent, execution_result={"status": "done"})
        await service.verify_task(task.id, tenant.id, agent, verification_result={"passed": True})
        recorded = await service.record_task(task.id, tenant.id)
        assert recorded.status == "recorded"


class TestTaskServiceInvalidTransitions:
    async def test_cannot_skip_to_executing(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Skip", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        with pytest.raises(TaskTransitionNotAllowed):
            await service.start_execution(task.id, tenant.id, agent)

    async def test_cannot_record_unverified(self, tenant, service):
        task = await service.create_task(tenant.id, title="Unverified", task_type="alert_triage")
        with pytest.raises(TaskTransitionNotAllowed):
            await service.record_task(task.id, tenant.id)

    async def test_terminal_state_rejects_transitions(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Cancel", task_type="alert_triage")
        cancelled = await service.cancel_task(task.id, tenant.id, reason="test")
        assert cancelled.status == "cancelled"
        with pytest.raises(TaskTransitionNotAllowed):
            await service.assign_task(task.id, tenant.id, agent)


class TestTaskServiceFailurePath:
    async def test_executing_to_failed(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Fail", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        await service.accept_task(task.id, tenant.id, agent)
        await service.start_investigation(task.id, tenant.id, agent)
        await service.start_execution(task.id, tenant.id, agent)
        failed = await service.fail_task(task.id, tenant.id, agent, failure_reason="execution error")
        assert failed.status == "failed"

    async def test_failed_to_retrying(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Retry", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        await service.accept_task(task.id, tenant.id, agent)
        await service.start_investigation(task.id, tenant.id, agent)
        await service.start_execution(task.id, tenant.id, agent)
        await service.fail_task(task.id, tenant.id, agent, failure_reason="temp error")
        retrying = await service.retry_task(task.id, tenant.id, agent)
        assert retrying.status == "retrying"
        assert retrying.retry_count == 1

    async def test_failed_to_escalated(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Escalate", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        await service.accept_task(task.id, tenant.id, agent)
        await service.start_investigation(task.id, tenant.id, agent)
        await service.start_execution(task.id, tenant.id, agent)
        await service.fail_task(task.id, tenant.id, agent, failure_reason="need help")
        escalated = await service.escalate_task(task.id, tenant.id, agent, target_agent_id=agent)
        assert escalated.status == "escalated"


class TestTaskServiceAudit:
    async def test_audit_log_grows_with_transitions(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Audit", task_type="alert_triage")
        initial_len = len(task.audit_log)
        await service.assign_task(task.id, tenant.id, agent)
        fetched = await service.get(task.id, tenant.id)
        assert len(fetched.audit_log) == initial_len + 1

    async def test_audit_records_actor(self, tenant, service, agent):
        task = await service.create_task(tenant.id, title="Actor Audit", task_type="alert_triage")
        await service.assign_task(task.id, tenant.id, agent)
        fetched = await service.get(task.id, tenant.id)
        last_entry = fetched.audit_log[-1]
        assert "assigned_agent_id" in last_entry
