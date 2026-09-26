"""Server-side assignment authorization (M9.12b).

The audited defect: ``POST /api/tasks/{task_id}/assign`` wrote
``assigned_agent_id`` straight onto the task without checking that the target
agent exists *inside the caller's tenant*, that it declares support for the
task's type, or that it is eligible to accept work. A frontend that hides
ineligible agents is never the control, so the route enforces all of it before
any write, and the assignment is audited against the authenticated principal.

These tests call the route function directly with the project's own service
factories: they exercise the real guards (tenant-scoped repository lookup, the
Agent model's ``can_accept_task`` predicate, the task-type capability rule, the
existing lifecycle validator) without standing up the HTTP/auth stack, which the
API suites already cover.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete

from app.api.routes.tasks import assign_task as assign_task_route
from app.api.routes.tasks import get_agent_service, get_task_service
from app.core.db import AsyncSessionLocal
from app.models.agent import Agent
from app.models.task import Task
from app.models.tenant import Tenant
from app.schemas.task import TaskAssign

pytestmark = pytest.mark.asyncio


def _principal(email: str = "harry@hsdeep.io") -> SimpleNamespace:
    """The shape ``_request_actor`` reads: a resolved principal on request.state."""
    return SimpleNamespace(
        state=SimpleNamespace(principal=SimpleNamespace(user_id=uuid.uuid4(), email=email))
    )


def _anonymous() -> SimpleNamespace:
    """A request that carried no principal (no human to attribute it to)."""
    return SimpleNamespace(state=SimpleNamespace())


async def _make_tenant(name: str) -> Tenant:
    async with AsyncSessionLocal() as db:
        t = Tenant(name=name, slug=f"assign-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    return t


async def _drop_tenant(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()


async def _make_agent(db, tenant_id: uuid.UUID, **overrides) -> Agent:
    """A real, eligible agent (every ``is_available_for_work`` predicate holds)."""
    fields = {
        "agent_id": f"AGT-{uuid.uuid4().hex[:6].upper()}",
        "name": "NOC Network Analyst 07",
        "role": "network_analyst",
        "department": "noc",
        "level": "l2",
        "is_active": True,
        "status": "active",
        "availability": "available",
        "current_workload": 0,
        "max_workload": 5,
        "skills": ["networking"],
        "capabilities": ["snmp"],
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
        "title": "Investigate WAN2",
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


@pytest_asyncio.fixture
async def tenant_a():
    t = await _make_tenant("Assignment Tenant A")
    yield t
    await _drop_tenant(t.id)


@pytest_asyncio.fixture
async def tenant_b():
    t = await _make_tenant("Assignment Tenant B")
    yield t
    await _drop_tenant(t.id)


class TestAssignmentAuthorization:
    """Tenant ownership, capability, eligibility and lifecycle, server-side."""

    async def test_cross_tenant_agent_is_rejected_and_nothing_changes(
        self, tenant_a, tenant_b
    ):
        """Tenant A must not assign Tenant B's agent - nor learn that the id exists."""
        async with AsyncSessionLocal() as db:
            foreign_agent = await _make_agent(db, tenant_b.id)
            task = await _make_task(db, tenant_a.id)

            with pytest.raises(HTTPException) as exc:
                await assign_task_route(
                    task_id=task.id,
                    request=_principal(),
                    body=TaskAssign(assigned_agent_id=foreign_agent.id),
                    service=get_task_service(db),
                    agent_service=get_agent_service(db),
                    tenant_id=tenant_a.id,
                    db=db,
                )

            assert exc.value.status_code == 404
            assert "tenant" in exc.value.detail.lower()
            # No cross-tenant leak and no partial write.
            await db.refresh(task)
            assert task.status == "created"
            assert task.assigned_agent_id is None
            assert task.assigned_at is None
            await db.refresh(foreign_agent)
            assert foreign_agent.current_workload == 0

    async def test_unknown_agent_is_rejected(self, tenant_a):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant_a.id)

            with pytest.raises(HTTPException) as exc:
                await assign_task_route(
                    task_id=task.id,
                    request=_principal(),
                    body=TaskAssign(assigned_agent_id=uuid.uuid4()),
                    service=get_task_service(db),
                    agent_service=get_agent_service(db),
                    tenant_id=tenant_a.id,
                    db=db,
                )

            assert exc.value.status_code == 404
            await db.refresh(task)
            assert task.status == "created"

    async def test_agent_that_does_not_support_the_task_type_is_rejected(self, tenant_a):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant_a.id, supported_task_types=["alert_triage"])
            task = await _make_task(db, tenant_a.id, task_type="firmware_upgrade")

            with pytest.raises(HTTPException) as exc:
                await assign_task_route(
                    task_id=task.id,
                    request=_principal(),
                    body=TaskAssign(assigned_agent_id=agent.id),
                    service=get_task_service(db),
                    agent_service=get_agent_service(db),
                    tenant_id=tenant_a.id,
                    db=db,
                )

            assert exc.value.status_code == 422
            assert "does not support task type" in exc.value.detail
            await db.refresh(task)
            assert task.status == "created"

    async def test_agent_at_capacity_is_rejected(self, tenant_a):
        """Reuses the Agent model's own ``can_accept_task`` predicate."""
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant_a.id, current_workload=5, max_workload=5)
            task = await _make_task(db, tenant_a.id)

            with pytest.raises(HTTPException) as exc:
                await assign_task_route(
                    task_id=task.id,
                    request=_principal(),
                    body=TaskAssign(assigned_agent_id=agent.id),
                    service=get_task_service(db),
                    agent_service=get_agent_service(db),
                    tenant_id=tenant_a.id,
                    db=db,
                )

            assert exc.value.status_code == 409
            assert "cannot accept work" in exc.value.detail
            await db.refresh(task)
            assert task.status == "created"

    async def test_inactive_agent_is_rejected(self, tenant_a):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant_a.id, status="offline")
            task = await _make_task(db, tenant_a.id)

            with pytest.raises(HTTPException) as exc:
                await assign_task_route(
                    task_id=task.id,
                    request=_principal(),
                    body=TaskAssign(assigned_agent_id=agent.id),
                    service=get_task_service(db),
                    agent_service=get_agent_service(db),
                    tenant_id=tenant_a.id,
                    db=db,
                )

            assert exc.value.status_code == 409


class TestAssignmentAudit:
    """Actor attribution: a human assignment is never recorded as SYSTEM."""

    async def test_human_assignment_is_successful_and_audited(self, tenant_a):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant_a.id)
            task = await _make_task(db, tenant_a.id)

            result = await assign_task_route(
                task_id=task.id,
                request=_principal("harry@hsdeep.io"),
                body=TaskAssign(assigned_agent_id=agent.id),
                service=get_task_service(db),
                agent_service=get_agent_service(db),
                tenant_id=tenant_a.id,
                db=db,
            )

            assert result.status == "assigned"
            assert result.assigned_agent_id == agent.id
            assert result.assigned_at is not None

            entry = result.audit_log[-1]
            assert entry["action"] == "assigned"
            assert entry["from_status"] == "created"
            assert entry["to_status"] == "assigned"
            # The authenticated human, not SYSTEM.
            assert entry["actor"] == "harry@hsdeep.io"
            assert entry["assigned_to"] == str(agent.id)

            # Workload moved along the same path.
            await db.refresh(agent)
            assert agent.current_workload == 1

    async def test_assignment_without_a_principal_is_not_attributed_to_a_human(
        self, tenant_a
    ):
        """No principal means no human to name: SYSTEM, never a guess."""
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant_a.id)
            task = await _make_task(db, tenant_a.id)

            result = await assign_task_route(
                task_id=task.id,
                request=_anonymous(),
                body=TaskAssign(assigned_agent_id=agent.id),
                service=get_task_service(db),
                agent_service=get_agent_service(db),
                tenant_id=tenant_a.id,
                db=db,
            )

            assert result.audit_log[-1]["actor"] == "SYSTEM"

    async def test_agent_driven_assignment_names_the_agent(self, tenant_a):
        async with AsyncSessionLocal() as db:
            assigning_agent = await _make_agent(db, tenant_a.id)
            agent = await _make_agent(db, tenant_a.id)
            task = await _make_task(db, tenant_a.id)

            result = await assign_task_route(
                task_id=task.id,
                request=_anonymous(),
                body=TaskAssign(
                    assigned_agent_id=agent.id,
                    assigned_by_agent_id=assigning_agent.id,
                ),
                service=get_task_service(db),
                agent_service=get_agent_service(db),
                tenant_id=tenant_a.id,
                db=db,
            )

            assert result.audit_log[-1]["actor"] == f"agent:{assigning_agent.id}"

    async def test_illegal_lifecycle_transition_is_still_refused(self, tenant_a):
        """The existing validator keeps owning the state machine."""
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant_a.id)
            task = await _make_task(db, tenant_a.id, status="completed")

            with pytest.raises(HTTPException) as exc:
                await assign_task_route(
                    task_id=task.id,
                    request=_principal(),
                    body=TaskAssign(assigned_agent_id=agent.id),
                    service=get_task_service(db),
                    agent_service=get_agent_service(db),
                    tenant_id=tenant_a.id,
                    db=db,
                )

            assert exc.value.status_code == 409
