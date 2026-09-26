"""
Task Repository — all direct SQLAlchemy access for Task model.
Follows the same repository pattern as IncidentRepository.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task


class TaskRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> Task:
        task = Task(**fields)
        self.db.add(task)
        await self.db.flush()
        await self.db.refresh(task)
        return task

    async def get(self, task_id: uuid.UUID, tenant_id: uuid.UUID) -> Task | None:
        stmt = select(Task).where(
            Task.id == task_id,
            Task.tenant_id == tenant_id,
            Task.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_task_number(self, task_number: str, tenant_id: uuid.UUID) -> Task | None:
        """Lookup by human-readable task number."""
        stmt = select(Task).where(
            Task.task_number == task_number,
            Task.tenant_id == tenant_id,
            Task.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        task_type: str | None = None,
        priority: str | None = None,
        assigned_agent_id: uuid.UUID | None = None,
        department: str | None = None,
        customer_id: str | None = None,
        project_id: str | None = None,
        correlation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        stmt = select(Task).where(
            Task.tenant_id == tenant_id,
            Task.is_deleted.is_(False),
        )
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if task_type is not None:
            stmt = stmt.where(Task.task_type == task_type)
        if priority is not None:
            stmt = stmt.where(Task.priority == priority)
        if assigned_agent_id is not None:
            stmt = stmt.where(Task.assigned_agent_id == assigned_agent_id)
        if customer_id is not None:
            stmt = stmt.where(Task.customer_id == customer_id)
        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        if correlation_id is not None:
            stmt = stmt.where(Task.correlation_id == correlation_id)
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_agent(
        self,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[Task]:
        """List tasks assigned to a specific agent."""
        stmt = select(Task).where(
            Task.assigned_agent_id == agent_id,
            Task.tenant_id == tenant_id,
            Task.is_deleted.is_(False),
        )
        if status is not None:
            stmt = stmt.where(Task.status == status)
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_incident(
        self,
        incident_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        limit: int = 50,
    ) -> list[Task]:
        """List tasks linked to a specific incident."""
        stmt = select(Task).where(
            Task.incident_id == incident_id,
            Task.tenant_id == tenant_id,
            Task.is_deleted.is_(False),
        ).order_by(Task.created_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_by_status(
        self,
        tenant_id: uuid.UUID,
        *,
        assigned_agent_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """Count tasks by status for dashboard/health views."""
        stmt = (
            select(Task.status, func.count())
            .where(
                Task.tenant_id == tenant_id,
                Task.is_deleted.is_(False),
            )
            .group_by(Task.status)
        )
        if assigned_agent_id is not None:
            stmt = stmt.where(Task.assigned_agent_id == assigned_agent_id)
        result = await self.db.execute(stmt)
        return {status: count for status, count in result.all()}

    async def next_task_number(self, tenant_id: uuid.UUID) -> str:
        """Generate the next task number for a tenant."""
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.tenant_id == tenant_id, Task.is_deleted.is_(False))
        )
        result = await self.db.execute(stmt)
        count = result.scalar_one() or 0
        return f"TSK-{count + 1:04d}"

    async def update(self, task: Task, **fields) -> Task:
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        await self.db.flush()
        await self.db.refresh(task)
        return task