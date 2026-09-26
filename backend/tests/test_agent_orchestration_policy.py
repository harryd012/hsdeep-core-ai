"""Phase 9.12 - human-controlled agent orchestration: the automation default.

The milestone's core rule is that agents do NOTHING on their own unless an
administrator has explicitly enabled automation. These tests pin that down on the
real code paths: the policy default, the automatic-assignment dispatcher, the
human assignment path (which must keep working), lifecycle enforcement, and actor
attribution in the audit trail.
"""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.scheduler import task_dispatch
from app.repositories.task_repository import TaskRepository
from app.services.task_service import TaskService, TaskTransitionNotAllowed

pytestmark = pytest.mark.asyncio


class TestAutomationPolicyDefault:
    async def test_automatic_task_assignment_is_off_by_default(self, monkeypatch):
        monkeypatch.delenv("AUTONOMOUS_TASK_ASSIGNMENT", raising=False)
        get_settings.cache_clear()
        try:
            assert get_settings().autonomous_task_assignment is False
        finally:
            get_settings.cache_clear()

    async def test_the_dispatcher_does_not_route_when_the_policy_is_off(self, monkeypatch):
        """With automation off, a queued task must stay unassigned.

        The dispatcher's real entry point is called: it must return before
        touching the database, so nothing can be silently assigned to an agent
        behind the operator's back.
        """
        monkeypatch.setenv("AUTONOMOUS_TASK_ASSIGNMENT", "false")
        get_settings.cache_clear()
        reached: list[str] = []

        class _Boom:
            def __getattr__(self, name):  # pragma: no cover - must never run
                reached.append(name)
                raise AssertionError("dispatch started a run with automation OFF")

        monkeypatch.setattr(task_dispatch, "engine", _Boom())
        try:
            await task_dispatch.dispatch_queued_tasks()
        finally:
            get_settings.cache_clear()
        assert reached == []

    async def test_the_policy_can_be_enabled_explicitly(self, monkeypatch):
        monkeypatch.setenv("AUTONOMOUS_TASK_ASSIGNMENT", "true")
        get_settings.cache_clear()
        try:
            assert get_settings().autonomous_task_assignment is True
        finally:
            get_settings.cache_clear()


class TestHumanAssignment:
    async def test_a_human_assigns_a_task(self, tenant, db):
        from app.models.agent import Agent

        worker = Agent(
            tenant_id=tenant.id,
            name="NOC Analyst 07",
            agent_id="AGT-TEST-A",
            level="L2",
            role="noc_analyst",
            department="noc",
            status="available",
        )
        db.add(worker)
        await db.flush()

        service = TaskService(TaskRepository(db))
        task = await service.create_task(
            tenant.id,
            title="Investigate flapping uplink",
            task_type="investigation",
            owner_command="harry@example.com",
        )
        # A human-created task starts UNASSIGNED: assignment is a separate,
        # deliberate human act.
        assert task.status == "created"
        assert task.assigned_agent_id is None
        assert task.audit_log[0]["actor"] == "harry@example.com"

        assigned = await service.assign_task(task.id, tenant.id, worker.id)
        assert assigned.status == "assigned"
        assert assigned.assigned_agent_id == worker.id
        assert assigned.assigned_at is not None
        await db.commit()

    async def test_an_explicit_actor_is_audited_and_never_leaks_into_the_row(
        self, tenant, db
    ):
        service = TaskService(TaskRepository(db))
        task = await service.create_task(
            tenant.id,
            title="Human-authored task",
            task_type="investigation",
            actor="harry@example.com",
        )
        # The actor is audit metadata, not a column: the insert still succeeded.
        assert task.id is not None
        assert task.audit_log[0]["actor"] == "harry@example.com"

    async def test_system_actor_is_only_used_for_genuinely_system_work(self, tenant, db):
        service = TaskService(TaskRepository(db))
        task = await service.create_task(
            tenant.id, title="Scheduled health sweep", task_type="health_check"
        )
        assert task.audit_log[0]["actor"] == "SYSTEM"

    async def test_invalid_lifecycle_transition_is_rejected(self, tenant, db):
        service = TaskService(TaskRepository(db))
        # created -> working is not a legal transition: only assignment may follow
        # creation, so the server refuses it rather than allowing a task to skip
        # the human assignment step.
        with pytest.raises(TaskTransitionNotAllowed):
            service._validate_transition("created", "working")
        # The legal path still validates.
        service._validate_transition("created", "assigned")
        service._validate_transition("assigned", "accepted")