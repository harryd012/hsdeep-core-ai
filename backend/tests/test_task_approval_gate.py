"""Task approval workflow and the protected-action gate (P0).

The security invariant this suite exists to prove:

    when a task requires approval, protected infrastructure execution MUST NOT
    START unless the persisted approval state says APPROVED.

The gate lives at the single boundary that can actually issue an external
action: ``TaskService.run_worker_execution`` resolves a ``tool_gateway`` adapter
and calls ``adapter.execute(...)``. The tests below register a recording stub
adapter and assert on its call list, so "blocked" means "the protected action
was never invoked", not merely "an exception was raised". No fake infrastructure
result is produced anywhere: the stub records calls and reports honestly.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete

from app.api.routes.tasks import approve_task as approve_task_route
from app.api.routes.tasks import get_task_service
from app.api.routes.tasks import reject_task as reject_task_route
from app.api.routes.tasks import request_task_approval as request_approval_route
from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.agent import Agent
from app.models.task import Task
from app.models.tenant import Tenant
from app.repositories.task_repository import TaskRepository
from app.schemas.task import TaskStatusChange
from app.services import tool_gateway
from app.services.task_service import ApprovalRequiredError, TaskService

pytestmark = pytest.mark.asyncio


class _RecordingAdapter:
    """A stub at the existing execution boundary. It never fakes a result."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, execution_input: dict, *, context: dict | None = None) -> dict:
        self.calls.append({"input": execution_input, "context": context})
        return {
            "success": True,
            "output": {"stub": True, "invocations": len(self.calls)},
            "error": None,
        }


def _principal(email: str = "harry@hsdeep.io") -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(principal=SimpleNamespace(user_id=uuid.uuid4(), email=email))
    )


def _anonymous() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


def _register_adapter() -> tuple[str, _RecordingAdapter]:
    """Register a uniquely named stub so no cleanup can race another test."""
    tool_name = f"test-tool-{uuid.uuid4().hex[:8]}"
    adapter = _RecordingAdapter()
    tool_gateway.register_adapter(tool_name, adapter)
    return tool_name, adapter


async def _make_tenant(name: str = "Approval Tenant") -> Tenant:
    async with AsyncSessionLocal() as db:
        t = Tenant(name=name, slug=f"approval-{uuid.uuid4().hex[:8]}")
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
        "status": "accepted",
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
    t = await _make_tenant("Other Approval Tenant")
    yield t
    await _drop_tenant(t.id)


class TestProtectedActionGate:
    """The Action Plane refuses protected execution unless approval_status is APPROVED."""

    async def test_waiting_task_blocks_execution_and_never_invokes_the_adapter(self, tenant):
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, approval_required=True
            )
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")

            with pytest.raises(ApprovalRequiredError):
                await service.run_worker_execution(
                    task.id, tenant.id, agent_id=agent.id, tool_name=tool,
                    execution_input={}, allowed_tools={tool},
                )

            assert adapter.calls == []  # the protected action never ran
            await db.refresh(task)
            assert task.status == "accepted"  # unexecuted, not advanced
            assert task.approval_status == "waiting_for_approval"

    async def test_rejected_task_blocks_execution(self, tenant):
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, approval_required=True
            )
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            await service.reject_task(
                task.id, tenant.id, actor="harry@hsdeep.io", reason="too risky"
            )

            with pytest.raises(ApprovalRequiredError):
                await service.run_worker_execution(
                    task.id, tenant.id, agent_id=agent.id, tool_name=tool,
                    execution_input={}, allowed_tools={tool},
                )

            assert adapter.calls == []

    async def test_required_but_unrequested_blocks_execution(self, tenant):
        """approval_required=True with status NOT_REQUIRED must still BLOCK."""
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, approval_required=True
            )
            with pytest.raises(ApprovalRequiredError):
                await get_task_service(db).run_worker_execution(
                    task.id, tenant.id, agent_id=agent.id, tool_name=tool,
                    execution_input={}, allowed_tools={tool},
                )
            assert adapter.calls == []

    async def test_approved_task_reaches_the_existing_action_plane(self, tenant):
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(
                db, tenant.id, assigned_agent_id=agent.id, approval_required=True
            )
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            await service.approve_task(task.id, tenant.id, actor="harry@hsdeep.io")

            result = await service.run_worker_execution(
                task.id, tenant.id, agent_id=agent.id, tool_name=tool,
                execution_input={"interface": "GigabitEthernet0/1"}, allowed_tools={tool},
            )

            assert len(adapter.calls) == 1  # executed exactly once, via the real boundary
            assert result.status == "verifying"
            actions = [e["action"] for e in result.audit_log]
            assert "approval_requested" in actions
            assert "approval_granted" in actions

    async def test_approval_not_required_preserves_existing_execution(self, tenant):
        tool, adapter = _register_adapter()
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id)
            task = await _make_task(db, tenant.id, assigned_agent_id=agent.id)
            assert task.approval_required is False
            await get_task_service(db).run_worker_execution(
                task.id, tenant.id, agent_id=agent.id, tool_name=tool,
                execution_input={}, allowed_tools={tool},
            )
            assert len(adapter.calls) == 1


class TestApprovalWorkflow:
    """Request / approve / reject: tenant-safe, authorized, audited."""

    async def test_request_approval_sets_waiting_and_records_authenticated_actor(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id, approval_required=True)
            result = await request_approval_route(
                task_id=task.id, request=_principal("harry@hsdeep.io"),
                body=TaskStatusChange(reason="needs sign-off"),
                service=get_task_service(db), tenant_id=tenant.id, db=db,
            )
            assert result.approval_status == "waiting_for_approval"
            assert result.approval_requested_by == "harry@hsdeep.io"
            assert result.approval_requested_at is not None
            assert result.audit_log[-1]["action"] == "approval_requested"
            assert result.audit_log[-1]["actor"] == "harry@hsdeep.io"

    async def test_request_approval_rejected_when_approval_not_required(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id)
            with pytest.raises(HTTPException) as exc:
                await request_approval_route(
                    task_id=task.id, request=_principal(), body=None,
                    service=get_task_service(db), tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 409

    async def test_approve_from_waiting_records_decider_and_audit(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id, approval_required=True)
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            result = await approve_task_route(
                task_id=task.id, request=_principal("approver@hsdeep.io"),
                body=TaskStatusChange(reason="change window approved"),
                approver_agent_id=None, service=service, tenant_id=tenant.id, db=db,
            )
            assert result.approval_status == "approved"
            assert result.approval_decided_by == "approver@hsdeep.io"
            assert result.approval_decided_at is not None
            assert result.audit_log[-1]["action"] == "approval_granted"

    async def test_approve_requires_waiting_state(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id, approval_required=True)
            with pytest.raises(HTTPException) as exc:
                await approve_task_route(
                    task_id=task.id, request=_principal(), body=None,
                    approver_agent_id=None, service=get_task_service(db),
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 409

    async def test_reject_records_decision_and_audit(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id, approval_required=True)
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            result = await reject_task_route(
                task_id=task.id, request=_principal("approver@hsdeep.io"),
                body=TaskStatusChange(reason="insufficient evidence"),
                approver_agent_id=None, service=service, tenant_id=tenant.id, db=db,
            )
            assert result.approval_status == "rejected"
            assert result.approval_decided_by == "approver@hsdeep.io"
            assert result.approval_result == "insufficient evidence"
            assert result.audit_log[-1]["action"] == "approval_rejected"

    async def test_cross_tenant_approve_and_reject_are_rejected(self, tenant, other_tenant):
        async with AsyncSessionLocal() as db:
            foreign = await _make_task(db, other_tenant.id, approval_required=True)
            service = get_task_service(db)
            with pytest.raises(HTTPException) as exc:
                await approve_task_route(
                    task_id=foreign.id, request=_principal(), body=None,
                    approver_agent_id=None, service=service, tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 404
            with pytest.raises(HTTPException) as exc2:
                await reject_task_route(
                    task_id=foreign.id, request=_principal(), body=None,
                    approver_agent_id=None, service=service, tenant_id=tenant.id, db=db,
                )
            assert exc2.value.status_code == 404
            await db.refresh(foreign)
            assert foreign.approval_status == "not_required"  # untouched


    async def test_agent_without_approval_authority_is_rejected(self, tenant):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, can_approve=False)
            task = await _make_task(db, tenant.id, approval_required=True)
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            with pytest.raises(HTTPException) as exc:
                await approve_task_route(
                    task_id=task.id, request=_principal(), body=None,
                    approver_agent_id=agent.id, service=service,
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 403
            await db.refresh(task)
            assert task.approval_status == "waiting_for_approval"

    async def test_approval_limit_is_enforced(self, tenant):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, can_approve=True, approval_limit="low")
            task = await _make_task(db, tenant.id, approval_required=True, risk_level="high")
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            with pytest.raises(HTTPException) as exc:
                await approve_task_route(
                    task_id=task.id, request=_principal(), body=None,
                    approver_agent_id=agent.id, service=service,
                    tenant_id=tenant.id, db=db,
                )
            assert exc.value.status_code == 403
            assert "approval_limit" in exc.value.detail
            await db.refresh(task)
            assert task.approval_status == "waiting_for_approval"

    async def test_agent_with_sufficient_limit_may_approve(self, tenant):
        async with AsyncSessionLocal() as db:
            agent = await _make_agent(db, tenant.id, can_approve=True, approval_limit="high")
            task = await _make_task(db, tenant.id, approval_required=True, risk_level="high")
            service = get_task_service(db)
            await service.request_approval(task.id, tenant.id, actor="harry@hsdeep.io")
            result = await approve_task_route(
                task_id=task.id, request=_principal("approver@hsdeep.io"), body=None,
                approver_agent_id=agent.id, service=service, tenant_id=tenant.id, db=db,
            )
            assert result.approval_status == "approved"
            assert result.approval_decided_by == "approver@hsdeep.io"

    async def test_existing_tasks_remain_valid_with_safe_defaults(self, tenant):
        async with AsyncSessionLocal() as db:
            task = await _make_task(db, tenant.id)
            assert task.approval_required is False
            assert task.approval_status == "not_required"
            assert task.approval_requested_at is None
            assert task.approval_decided_at is None
            stored = await TaskRepository(db).get(task.id, tenant.id)
            assert stored is not None and stored.approval_status == "not_required"

    async def test_autonomous_task_assignment_remains_false(self):
        assert get_settings().autonomous_task_assignment is False
