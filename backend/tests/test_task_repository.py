"""
Task Repository tests — validates the data access layer for Task model.

Uses the real PostgreSQL DB (same convention as conftest.py).
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.task import Task
from app.models.tenant import Tenant
from app.repositories.task_repository import TaskRepository

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Task Repo Test", slug=f"task-repo-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)
    yield t
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


@pytest_asyncio.fixture
async def repo():
    async with AsyncSessionLocal() as db:
        yield TaskRepository(db)
        await db.rollback()


async def _make_task(repo: TaskRepository, tenant_id: uuid.UUID, **overrides) -> Task:
    fields = {
        "task_number": f"TSK-{uuid.uuid4().hex[:6].upper()}",
        "title": "Test Task",
        "task_type": "alert_triage",
        "status": "created",
    }
    fields.update(overrides)
    return await repo.create(tenant_id=tenant_id, **fields)


class TestTaskRepositoryCreate:
    async def test_create_task(self, tenant, repo):
        task = await _make_task(repo, tenant.id, title="Investigate Alert")
        assert task.id is not None
        assert task.tenant_id == tenant.id
        assert task.title == "Investigate Alert"
        assert task.status == "created"

    async def test_create_generates_uuid(self, tenant, repo):
        task = await _make_task(repo, tenant.id)
        assert isinstance(task.id, uuid.UUID)

    async def test_create_with_all_fields(self, tenant, repo):
        task = await _make_task(
            repo, tenant.id, priority="P1", complexity="high",
            required_skills=["linux", "networking"], customer_id="CUST-001",
        )
        assert task.priority == "P1"
        assert task.complexity == "high"
        assert "linux" in task.required_skills
        assert task.customer_id == "CUST-001"


class TestTaskRepositoryGet:
    async def test_get_by_id(self, tenant, repo):
        created = await _make_task(repo, tenant.id, title="Fetch Task")
        fetched = await repo.get(created.id, tenant.id)
        assert fetched is not None
        assert fetched.id == created.id

    async def test_get_returns_none_for_wrong_tenant(self, tenant, repo):
        created = await _make_task(repo, tenant.id)
        fetched = await repo.get(created.id, uuid.uuid4())
        assert fetched is None

    async def test_get_by_task_number(self, tenant, repo):
        created = await _make_task(repo, tenant.id, task_number="TSK-9999")
        fetched = await repo.get_by_task_number("TSK-9999", tenant.id)
        assert fetched is not None
        assert fetched.task_number == "TSK-9999"


class TestTaskRepositoryList:
    async def test_list_all(self, tenant, repo):
        for i in range(3):
            await _make_task(repo, tenant.id, title=f"Task {i}")
        tasks = await repo.list(tenant.id)
        assert len(tasks) >= 3

    async def test_list_filter_by_status(self, tenant, repo):
        await _make_task(repo, tenant.id, status="created")
        await _make_task(repo, tenant.id, status="assigned")
        tasks = await repo.list(tenant.id, status="assigned")
        assert all(t.status == "assigned" for t in tasks)

    async def test_list_filter_by_priority(self, tenant, repo):
        await _make_task(repo, tenant.id, priority="P1")
        await _make_task(repo, tenant.id, priority="P3")
        tasks = await repo.list(tenant.id, priority="P1")
        assert all(t.priority == "P1" for t in tasks)

    async def test_list_filter_by_task_type(self, tenant, repo):
        await _make_task(repo, tenant.id, task_type="alert_triage")
        await _make_task(repo, tenant.id, task_type="patch_management")
        tasks = await repo.list(tenant.id, task_type="patch_management")
        assert all(t.task_type == "patch_management" for t in tasks)

    async def test_list_filter_by_customer(self, tenant, repo):
        await _make_task(repo, tenant.id, customer_id="CUST-A")
        await _make_task(repo, tenant.id, customer_id="CUST-B")
        tasks = await repo.list(tenant.id, customer_id="CUST-A")
        assert all(t.customer_id == "CUST-A" for t in tasks)
