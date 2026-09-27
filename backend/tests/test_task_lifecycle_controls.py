"""Enterprise task lifecycle controls: reassignment and cancellation (M9.13).

These tests exercise the EXISTING lifecycle and the EXISTING protected-action
boundary. Where a test asserts that an action did not happen, it asserts on the
call list of a recording stub adapter registered through ``tool_gateway`` - the
real external-action boundary - not on a mocked service.

Kept deliberately separate from ``test_task_approval_gate.py``: that suite stays
the gate's regression net, this one covers the lifecycle controls around it.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete

from app.api.routes.tasks import cancel_task as cancel_task_route
from app.api.routes.tasks import get_agent_service, get_task_service
from app.api.routes.tasks import reassign_task as reassign_task_route
from app.api.routes.tasks import start_execution as start_execution_route
from app.core.db import AsyncSessionLocal
from app.models.agent import Agent
from app.models.task import Task
from app.models.tenant import Tenant
from app.schemas.task import TaskAssign, TaskStatusChange
from app.services import tool_gateway
from app.services.task_service import (
    ApprovalRequiredError,
    TaskTransitionNotAllowed,
)

pytestmark = pytest.mark.asyncio


class _RecordingAdapter:
    """Stub at the existing execution boundary. Records calls, fakes nothing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, execution_input: dict, *, context: dict | None = None) -> dict:
        self.calls.append({"input": execution_input, "context": context})
        return {"success": True, "output": {"stub": True}, "error": None}


def _principal(email: str = "harry@hsdeep.io") -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(principal=SimpleNamespace(user_id=uuid.uuid4(), email=email))
    )


def _register_adapter() -> tuple[str, _RecordingAdapter]:
    tool_name = f"test-tool-{uuid.uuid4().hex[:8]}"
    adapter = _RecordingAdapter()
    tool_gateway.register_adapter(tool_name, adapter)
    return tool_name, adapter


async def _make_tenant(name: str = "Lifecycle Tenant") -> Tenant:
    async with AsyncSessionLocal() as db:
        t = Tenant(name=name, slug=f"lifecycle-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    return t


async def _drop_tenant(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()


async def _make_agent(db, tenant_id: uuid.UUID, **overrides) -> Agent:
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
        "title": "Apply protected change to WAN2",
        "task_type": "alert_triage",
        "status": "assigned",
        "priority": "P3",
        "risk_level": "high",
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
async def tenant():
    t = await _make_tenant()
    yield t
    await _drop_tenant(t.id)


@pytest_asyncio.fixture
async def other_tenant():
    t = await _make_tenant("Other Lifecycle Tenant")
    yield t
    await _drop_tenant(t.id)


class TestReassignment:
    """Reassignment reuses the existing machine and the shared authz helper."""

    async def test_reassign_moves_the_task_and_is_auditable(self, tenant):
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id, current_workload=1)
            agent_b = await _make_agent(db, tenant.id)
            task = await _make_task(db, tenant.id, assigned_agent_id=agent_a.id)

            result = await reassign_task_route(
                task_id=task.id, request=_principal("harry@hsdeep.io"),
                body=TaskAssign(assigned_agent_id=agent_b.id), reason="shift change",
                service=get_task_service(db), agent_service=get_agent_service(db),
                tenant_id=tenant.id, db=db,
            )

            assert result.status == "assigned"
            assert result.assigned_agent_id == agent_b.id
            assert result.assigned_at is not None
            entries = result.audit_log[-2:]
            assert [e["action"] for e in entries] == ["reassigned", "assigned"]
            assert entries[0]["actor"] == "harry@hsdeep.io"
            assert entries[0]["previous_agent_id"] == str(agent_a.id)
            assert entries[0]["new_agent_id"] == str(agent_b.id)
            assert entries[1]["assigned_to"] == str(agent_b.id)

            # Workload follows the work, through the existing counters.
            await db.refresh(agent_a)
            await db.refresh(agent_b)
            assert agent_a.current_workload == 0
            assert agent_b.current_workload == 1

    async def test_reassign_does_not_touch_approval_state(self, tenant):
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            agent_b = await _make_agent(db, tenant.id)
            # Persisted state set directly: the lifecycle requests approval AFTER
            # acceptance, while reassignment applies to assigned/failed work, so
            # this pairing is established as state rather than via a request that
            # the (correct) precondition would refuse.
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent_a.id, approval_required=True,
                approval_status="waiting_for_approval",
                approval_requested_by="harry@hsdeep.io",
            )
            service = get_task_service(db)

            result = await reassign_task_route(
                task_id=task.id, request=_principal(),
                body=TaskAssign(assigned_agent_id=agent_b.id),
                reason=None, service=service, agent_service=get_agent_service(db),
                tenant_id=tenant.id, db=db,
            )

            assert result.approval_status == "waiting_for_approval"
            assert result.approval_requested_by == "harry@hsdeep.io"
            assert result.approval_decided_by is None

    async def test_reassign_does_not_authorize_execution(self, tenant):
        """A waiting task stays blocked after reassignment (invariant F)."""
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            agent_b = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent_a.id, approval_required=True,
                approval_status="waiting_for_approval",
                approval_requested_by="harry@hsdeep.io",
            )
            service = get_task_service(db)
            await reassign_task_route(
                task_id=task.id, request=_principal(),
                body=TaskAssign(assigned_agent_id=agent_b.id),
                reason=None, service=service, agent_service=get_agent_service(db),
                tenant_id=tenant.id, db=db,
            )

            with pytest.raises(ApprovalRequiredError):
                await service.run_worker_execution(
                    task.id, tenant.id, agent_id=agent_b.id, tool_name=tool,
                    execution_input={}, allowed_tools={tool},
                )
            assert adapter.calls == []

    async def test_reassign_of_approved_task_keeps_approval_and_still_executes(self, tenant):
        """Approval is neither granted nor revoked by reassignment (E, J)."""
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            agent_b = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent_a.id, approval_required=True,
                approval_status="approved", approval_decided_by="approver@hsdeep.io",
            )
            service = get_task_service(db)

            result = await reassign_task_route(
                task_id=task.id, request=_principal(),
                body=TaskAssign(assigned_agent_id=agent_b.id),
                reason=None, service=service, agent_service=get_agent_service(db),
                tenant_id=tenant.id, db=db,
            )
            assert result.approval_status == "approved"
            assert result.approval_decided_by == "approver@hsdeep.io"

            # Realistic continuation: the new agent accepts, then executes.
            await service.accept_task(task.id, tenant.id, agent_b.id)
            executed = await service.run_worker_execution(
                task.id, tenant.id, agent_id=agent_b.id, tool_name=tool,
                execution_input={}, allowed_tools={tool},
            )
            assert len(adapter.calls) == 1
            assert executed.status == "verifying"

    async def test_reassign_to_the_same_agent_is_deterministic(self, tenant):
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            task = await _make_task(db, tenant.id, assigned_agent_id=agent_a.id)
            with pytest.raises(HTTPException) as exc:
                await reassign_task_route(
                    task_id=task.id, request=_principal(),
                    body=TaskAssign(assigned_agent_id=agent_a.id),
                    service=get_task_service(db), agent_service=get_agent_service(db),
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 409
            await db.refresh(task)
            assert task.assigned_agent_id == agent_a.id


    async def test_reassign_terminal_task_is_refused(self, tenant):
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            agent_b = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent_a.id, status="cancelled"
            )
            with pytest.raises(HTTPException) as exc:
                await reassign_task_route(
                    task_id=task.id, request=_principal(),
                    body=TaskAssign(assigned_agent_id=agent_b.id),
                    service=get_task_service(db), agent_service=get_agent_service(db),
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 409
            await db.refresh(task)
            assert task.status == "cancelled"
            assert task.assigned_agent_id == agent_a.id

    async def test_reassign_cross_tenant_target_agent_is_refused(self, tenant, other_tenant):
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            foreign_agent = await _make_agent(db, other_tenant.id)
            task = await _make_task(db, tenant.id, assigned_agent_id=agent_a.id)

            with pytest.raises(HTTPException) as exc:
                await reassign_task_route(
                    task_id=task.id, request=_principal(),
                    body=TaskAssign(assigned_agent_id=foreign_agent.id),
                    service=get_task_service(db), agent_service=get_agent_service(db),
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 404
            await db.refresh(task)
            assert task.assigned_agent_id == agent_a.id
            await db.refresh(foreign_agent)
            assert foreign_agent.current_workload == 0

    async def test_reassign_cross_tenant_task_is_not_exposed(self, tenant, other_tenant):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            foreign_task = await _make_task(db, other_tenant.id, assigned_agent_id=None)

            with pytest.raises(HTTPException) as exc:
                await reassign_task_route(
                    task_id=foreign_task.id, request=_principal(),
                    body=TaskAssign(assigned_agent_id=agent.id),
                    service=get_task_service(db), agent_service=get_agent_service(db),
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 404
            await db.refresh(foreign_task)
            assert foreign_task.assigned_agent_id is None

    async def test_reassign_agent_that_cannot_do_the_task_type_is_refused(self, tenant):
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            specialist = await _make_agent(db, tenant.id, supported_task_types=["other_type"])
            task = await _make_task(db, tenant.id, assigned_agent_id=agent_a.id)
            with pytest.raises(HTTPException) as exc:
                await reassign_task_route(
                    task_id=task.id, request=_principal(),
                    body=TaskAssign(assigned_agent_id=specialist.id),
                    service=get_task_service(db), agent_service=get_agent_service(db),
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 422

    async def test_reassign_to_agent_at_capacity_is_refused(self, tenant):
        async with AsyncSessionLocal() as db:
            agent_a = await _make_agent(db, tenant.id)
            busy = await _make_agent(db, tenant.id, current_workload=5, max_workload=5)
            task = await _make_task(db, tenant.id, assigned_agent_id=agent_a.id)
            with pytest.raises(HTTPException) as exc:
                await reassign_task_route(
                    task_id=task.id, request=_principal(),
                    body=TaskAssign(assigned_agent_id=busy.id),
                    service=get_task_service(db), agent_service=get_agent_service(db),
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 409


class TestCancellation:
    """Cancellation stays inside the declared matrix and is audited."""

    async def test_cancel_records_authenticated_actor_and_is_terminal(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id, status="created", assigned_agent_id=None)
            result = await cancel_task_route(
                task_id=task.id, request=_principal("harry@hsdeep.io"),
                body=TaskStatusChange(reason="duplicate request"),
                service=get_task_service(db), tenant_id=tenant.id, db=db,
            )
            assert result.status == "cancelled"
            assert result.completed_at is not None
            entry = result.audit_log[-1]
            assert entry["action"] == "cancelled"
            assert entry["actor"] == "harry@hsdeep.io"
            assert entry["reason"] == "duplicate request"

            # Duplicate cancellation is a deterministic conflict, never a
            # silent success.
            with pytest.raises(HTTPException) as exc:
                await cancel_task_route(
                    task_id=task.id, request=_principal(), body=None,
                    service=get_task_service(db), tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 409

    async def test_cancelled_task_never_reaches_the_adapter(self, tenant):
        """Phase 8 mandatory: cancelled => the protected action cannot run."""
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, status="assigned"
            )
            service = get_task_service(db)
            await service.cancel_task(task.id, tenant.id, reason="no longer needed")

            with pytest.raises(TaskTransitionNotAllowed):
                await service.run_worker_execution(
                    task.id, tenant.id, agent_id=agent.id, tool_name=tool,
                    execution_input={}, allowed_tools={tool},
                )
            assert adapter.calls == []
            await db.refresh(task)
            assert task.status == "cancelled"

    async def test_cancel_is_refused_for_in_flight_states(self, tenant):
        """States with no `cancelled` edge are refused via the existing matrix."""
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id, status="executing")
            with pytest.raises(HTTPException) as exc:
                await cancel_task_route(
                    task_id=task.id, request=_principal(), body=None,
                    service=get_task_service(db), tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 409
            await db.refresh(task)
            assert task.status == "executing"

    async def test_cancel_cross_tenant_is_refused_and_foreign_state_untouched(
        self, tenant, other_tenant
    ):
        async with AsyncSessionLocal() as db:
            foreign = await _make_task(db, other_tenant.id, status="created")
            with pytest.raises(HTTPException) as exc:
                await cancel_task_route(
                    task_id=foreign.id, request=_principal(), body=None,
                    service=get_task_service(db), tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 404
            await db.refresh(foreign)
            assert foreign.status == "created"

    async def test_cancel_does_not_change_approval_state(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(
                db, tenant.id, status="accepted", approval_required=True
            )
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")

            result = await cancel_task_route(
                task_id=task.id, request=_principal(), body=None,
                service=service, tenant_id=tenant.id, db=db,
            )
            assert result.approval_status == "waiting_for_approval"


class TestExecutionGateOnLifecycle:
    """The gate also guards the transition INTO executing (invariant B)."""

    async def test_start_execution_is_gated_by_approval(self, tenant):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, status="investigating",
                approval_required=True,
            )
            with pytest.raises(ApprovalRequiredError):
                await get_task_service(db).start_execution(
                    task.id, tenant.id, agent.id, execution_plan={"steps": []}
                )
            await db.refresh(task)
            assert task.status == "investigating"  # not advanced as if executed

    async def test_start_execution_route_maps_refusal_to_403(self, tenant):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, status="investigating",
                approval_required=True,
            )
            with pytest.raises(HTTPException) as exc:
                await start_execution_route(
                    task_id=task.id, agent_id=agent.id, execution_plan=None,
                    service=get_task_service(db), tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 403

    async def test_start_execution_proceeds_when_approved(self, tenant):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, status="investigating",
                approval_required=True,
            )
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            await service.approve_task(task.id, tenant.id, actor="harry@hsdeep.io")

            result = await service.start_execution(task.id, tenant.id, agent.id)
            assert result.status == "executing"

    async def test_start_execution_unchanged_when_approval_not_required(self, tenant):
        """Invariant A: no behavioural change for ordinary tasks."""
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, status="investigating"
            )
            result = await get_task_service(db).start_execution(
                task.id, tenant.id, agent.id
            )
            assert result.status == "executing"
