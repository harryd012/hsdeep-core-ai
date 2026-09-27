"""Concurrency guards for the task lifecycle (Phase 15).

The lifecycle previously read a task, validated its state, then wrote - three
steps a second concurrent request could interleave, letting both succeed at a
mutually exclusive transition (two executions, or two approval decisions). The
protection is a row lock taken at the fetch that begins each transition.

These tests are deterministic on purpose: they prove (a) the locking fetch
really compiles to ``FOR UPDATE``, (b) every state-changing transition asks for
it, and (c) PostgreSQL genuinely serialises a second locker while the first
transaction is open. No sleeps and no timing assumptions.
"""
from __future__ import annotations

import types
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app.core.db import AsyncSessionLocal
from app.models.task import Task
from app.models.tenant import Tenant
from app.repositories.task_repository import TaskRepository
from app.services.task_service import TaskService

pytestmark = pytest.mark.asyncio


class _Stop(Exception):
    """Sentinel: stops a transition immediately after its locking fetch."""


class _CapturingResult:
    def scalar_one_or_none(self):
        return None


class _CapturingSession:
    """Records the statement the repository would run, without touching a DB."""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _CapturingResult()


async def _make_tenant() -> Tenant:
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Concurrency Tenant", slug=f"conc-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    return t


async def _drop_tenant(tenant_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()


async def _make_task(db, tenant_id: uuid.UUID, **overrides) -> Task:
    fields = {
        "task_number": f"TSK-{uuid.uuid4().hex[:6].upper()}",
        "title": "Concurrency Task",
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
async def tenant():
    t = await _make_tenant()
    yield t
    await _drop_tenant(t.id)


class TestLockingFetch:
    """The locking fetch is real SQL - and only when it is asked for."""

    async def test_for_update_fetch_compiles_to_a_row_lock(self):
        session = _CapturingSession()
        await TaskRepository(session).get(uuid.uuid4(), uuid.uuid4(), for_update=True)
        sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" in sql.upper()

    async def test_ordinary_fetch_takes_no_lock(self):
        session = _CapturingSession()
        await TaskRepository(session).get(uuid.uuid4(), uuid.uuid4())
        sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE" not in sql.upper()


class TestMutatingTransitionsRequestTheLock:
    """Every mutually exclusive transition goes through the locking fetch."""

    async def test_all_state_changing_transitions_pass_for_update(self, tenant):
        seen: list[bool] = []

        async def spy(self, task_id, tenant_id, *, for_update=False):
            seen.append(for_update)
            raise _Stop()

        service = TaskService.__new__(TaskService)  # get() is spied, so no repo
        service.get = types.MethodType(spy, service)
        task_id = uuid.uuid4()

        probes = [
            lambda: service.assign_task(task_id, tenant.id, uuid.uuid4()),
            lambda: service.reassign_task(task_id, tenant.id, uuid.uuid4()),
            lambda: service.accept_task(task_id, tenant.id, uuid.uuid4()),
            lambda: service.start_investigation(task_id, tenant.id, uuid.uuid4()),
            lambda: service.start_execution(task_id, tenant.id, uuid.uuid4()),
            lambda: service.cancel_task(task_id, tenant.id),
            lambda: service.request_approval(
                task_id, tenant.id, actor="harry@hsdeep.io"
            ),
            lambda: service.approve_task(task_id, tenant.id, actor="harry@hsdeep.io"),
            lambda: service.reject_task(task_id, tenant.id, actor="harry@hsdeep.io"),
            lambda: service.run_worker_execution(
                task_id, tenant.id, agent_id=uuid.uuid4()
            ),
        ]

        for probe in probes:
            with pytest.raises(_Stop):
                await probe()

        assert len(seen) == len(probes)
        assert all(seen), "every state-changing transition must take the row lock"


class TestRealSerialisation:
    """A second locker waits for the first transaction - proven against Postgres."""

    async def test_second_locker_blocks_until_the_first_transaction_ends(self, tenant):
        async with AsyncSessionLocal() as setup:
            task = await _make_task(setup, tenant.id)

        # Both sessions are context-managed so their transactions and
        # connections are released deterministically, in order, on every path.
        async with AsyncSessionLocal() as holder, AsyncSessionLocal() as waiter:
            holder_repo = TaskRepository(holder)
            # The holder takes the row lock and keeps its transaction open.
            held = await holder_repo.get(task.id, tenant.id, for_update=True)
            assert held is not None

            waiter_repo = TaskRepository(waiter)
            # Ask Postgres itself to give up waiting instead of cancelling the
            # Python coroutine mid-query: a clean LockNotAvailable error is
            # deterministic and leaves no half-executed statement behind.
            await waiter.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(DBAPIError) as exc:
                await waiter_repo.get(task.id, tenant.id, for_update=True)
            assert "lock" in str(exc.value).lower()

            # Clear the aborted transaction, then prove the lock is released
            # once the holder finishes.
            await waiter.rollback()
            await holder.rollback()

            after = await waiter_repo.get(task.id, tenant.id, for_update=True)
            assert after is not None
            assert after.id == task.id
