"""
Agent Repository — all direct SQLAlchemy access for Agent model.
Follows the same repository pattern as IncidentRepository.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent


class AgentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> Agent:
        agent = Agent(**fields)
        self.db.add(agent)
        await self.db.flush()
        await self.db.refresh(agent)
        return agent

    async def get(self, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> Agent | None:
        stmt = select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
            Agent.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_agent_id(self, agent_id_str: str, tenant_id: uuid.UUID) -> Agent | None:
        """Lookup by human-readable agent_id (e.g. 'CTO-001')."""
        stmt = select(Agent).where(
            Agent.agent_id == agent_id_str,
            Agent.tenant_id == tenant_id,
            Agent.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        department: str | None = None,
        level: str | None = None,
        role: str | None = None,
        status: str | None = None,
        availability: str | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Agent]:
        stmt = select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.is_deleted.is_(False),
        )
        if department is not None:
            stmt = stmt.where(Agent.department == department)
        if level is not None:
            stmt = stmt.where(Agent.level == level)
        if role is not None:
            stmt = stmt.where(Agent.role == role)
        if status is not None:
            stmt = stmt.where(Agent.status == status)
        if availability is not None:
            stmt = stmt.where(Agent.availability == availability)
        if is_active is not None:
            stmt = stmt.where(Agent.is_active == is_active)
        stmt = stmt.order_by(Agent.level, Agent.role).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_manager(
        self, manager_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> list[Agent]:
        """List all direct reports for a manager agent."""
        stmt = select(Agent).where(
            Agent.manager_id == manager_id,
            Agent.tenant_id == tenant_id,
            Agent.is_deleted.is_(False),
        ).order_by(Agent.role)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_available(
        self,
        tenant_id: uuid.UUID,
        *,
        department: str | None = None,
        level: str | None = None,
        limit: int = 50,
    ) -> list[Agent]:
        """List agents available for new work assignments."""
        stmt = select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.is_deleted.is_(False),
            Agent.is_active.is_(True),
            Agent.status == "active",
            Agent.availability.in_(["available", "busy"]),
        )
        if department is not None:
            stmt = stmt.where(Agent.department == department)
        if level is not None:
            stmt = stmt.where(Agent.level == level)
        stmt = stmt.order_by(Agent.current_workload.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_skills(
        self,
        tenant_id: uuid.UUID,
        skills: list[str],
        *,
        department: str | None = None,
        level: str | None = None,
        limit: int = 50,
    ) -> list[Agent]:
        """List agents matching ANY of the given skills."""
        stmt = select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.is_deleted.is_(False),
            Agent.is_active.is_(True),
            Agent.skills.overlap(skills),
        )
        if department is not None:
            stmt = stmt.where(Agent.department == department)
        if level is not None:
            stmt = stmt.where(Agent.level == level)
        stmt = stmt.order_by(Agent.current_workload.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_by_task_type(
        self,
        tenant_id: uuid.UUID,
        task_type: str,
        *,
        limit: int = 50,
    ) -> list[Agent]:
        """List agents that support a specific task type."""
        stmt = select(Agent).where(
            Agent.tenant_id == tenant_id,
            Agent.is_deleted.is_(False),
            Agent.is_active.is_(True),
            Agent.supported_task_types.contains([task_type]),
        ).order_by(Agent.current_workload.asc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update(self, agent: Agent, **fields) -> Agent:
        for key, value in fields.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        await self.db.flush()
        await self.db.refresh(agent)
        return agent

    async def increment_workload(self, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Atomically increment an agent's current workload."""
        agent = await self.get(agent_id, tenant_id)
        if agent is not None:
            agent.current_workload += 1
            await self.db.flush()

    async def decrement_workload(self, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Atomically decrement an agent's current workload."""
        agent = await self.get(agent_id, tenant_id)
        if agent is not None and agent.current_workload > 0:
            agent.current_workload -= 1
            await self.db.flush()

    async def record_task_completion(
        self,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        success: bool = True,
        duration_minutes: float | None = None,
    ) -> None:
        """Record task completion/failure for performance tracking."""
        agent = await self.get(agent_id, tenant_id)
        if agent is None:
            return
        if success:
            agent.tasks_completed += 1
        else:
            agent.tasks_failed += 1
        if duration_minutes is not None:
            total = agent.tasks_completed + agent.tasks_failed
            if total == 1:
                agent.avg_resolution_minutes = duration_minutes
            else:
                prev = agent.avg_resolution_minutes or 0
                agent.avg_resolution_minutes = round(
                    (prev * (total - 1) + duration_minutes) / total, 1
                )
        await self.db.flush()
