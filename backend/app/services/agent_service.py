"""
Agent Service — manages the virtual company agent registry.
Follows the same service pattern as IncidentService.
"""
from __future__ import annotations

import uuid

from app.models.agent import Agent
from app.repositories.agent_repository import AgentRepository


class AgentNotFound(Exception):
    pass


class AgentService:
    def __init__(self, agent_repo: AgentRepository):
        self.agent_repo = agent_repo

    async def get(self, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> Agent:
        agent = await self.agent_repo.get(agent_id, tenant_id)
        if agent is None:
            raise AgentNotFound(f"Agent {agent_id} not found")
        return agent

    async def get_by_agent_id(self, agent_id_str: str, tenant_id: uuid.UUID) -> Agent:
        agent = await self.agent_repo.get_by_agent_id(agent_id_str, tenant_id)
        if agent is None:
            raise AgentNotFound(f"Agent with agent_id '{agent_id_str}' not found")
        return agent

    async def list_agents(self, tenant_id: uuid.UUID, **filters) -> list[Agent]:
        return await self.agent_repo.list(tenant_id, **filters)

    async def list_by_manager(self, manager_id: uuid.UUID, tenant_id: uuid.UUID) -> list[Agent]:
        """List direct reports for a manager."""
        return await self.agent_repo.list_by_manager(manager_id, tenant_id)

    async def list_available(self, tenant_id: uuid.UUID, **filters) -> list[Agent]:
        """List agents available for work."""
        return await self.agent_repo.list_available(tenant_id, **filters)

    async def list_by_skills(
        self, tenant_id: uuid.UUID, skills: list[str], **filters
    ) -> list[Agent]:
        """Find agents matching specific skills."""
        return await self.agent_repo.list_by_skills(tenant_id, skills, **filters)

    async def list_by_task_type(
        self, tenant_id: uuid.UUID, task_type: str, **filters
    ) -> list[Agent]:
        """Find agents that support a specific task type."""
        return await self.agent_repo.list_by_task_type(tenant_id, task_type, **filters)

    async def create_agent(self, tenant_id: uuid.UUID, **fields) -> Agent:
        """Register a new agent in the organization."""
        return await self.agent_repo.create(tenant_id=tenant_id, **fields)

    async def update_agent(
        self, agent_id: uuid.UUID, tenant_id: uuid.UUID, **fields
    ) -> Agent:
        """Update an agent's profile."""
        agent = await self.get(agent_id, tenant_id)
        return await self.agent_repo.update(agent, **fields)

    async def set_availability(
        self, agent_id: uuid.UUID, tenant_id: uuid.UUID, availability: str
    ) -> Agent:
        """Set an agent's availability status."""
        agent = await self.get(agent_id, tenant_id)
        return await self.agent_repo.update(agent, availability=availability)

    async def assign_task(
        self, agent_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> None:
        """Increment workload when assigning a task."""
        await self.agent_repo.increment_workload(agent_id, tenant_id)

    async def release_task(self, agent_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Give back one unit of workload when work leaves an agent.

        Reuses the existing repository counter so reassignment does not leave
        the previous agent's workload inflated. Deliberately does NOT record a
        completion or failure statistic - the work did not finish, it was moved,
        so ``complete_task`` would be the wrong call here.
        """
        await self.agent_repo.decrement_workload(agent_id, tenant_id)

    async def complete_task(
        self,
        agent_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        success: bool = True,
        duration_minutes: float | None = None,
    ) -> None:
        """Record task completion and update workload."""
        await self.agent_repo.record_task_completion(
            agent_id, tenant_id, success=success, duration_minutes=duration_minutes
        )
        await self.agent_repo.decrement_workload(agent_id, tenant_id)

    async def get_cto(self, tenant_id: uuid.UUID) -> Agent | None:
        """Get the CTO agent for a tenant (convenience method)."""
        agents = await self.agent_repo.list(
            tenant_id, role="cto", level="cto", limit=1
        )
        return agents[0] if agents else None

    async def get_manager(
        self, tenant_id: uuid.UUID, department: str
    ) -> Agent | None:
        """Get the manager agent for a specific department."""
        agents = await self.agent_repo.list(
            tenant_id, department=department, level="manager", limit=1
        )
        return agents[0] if agents else None

    async def get_organization_tree(
        self, tenant_id: uuid.UUID
    ) -> list[dict]:
        """Build the full organization hierarchy tree."""
        all_agents = await self.agent_repo.list(tenant_id, limit=1000)
        # Only active agents are part of the live org topology (consistent with
        # CommandCenterService._build_tree). Inactive/decommissioned agents are
        # excluded from the tree but never deleted, so task history that
        # references them still resolves by id.
        all_agents = [a for a in all_agents if a.is_active]

        # Build lookup maps
        by_id: dict[uuid.UUID, dict] = {}
        roots: list[dict] = []

        for agent in all_agents:
            node = {
                "id": agent.id,
                "agent_id": agent.agent_id,
                "name": agent.name,
                "role": agent.role,
                "department": agent.department,
                "level": agent.level,
                "status": agent.status,
                "availability": agent.availability,
                "current_workload": agent.current_workload,
                "max_workload": agent.max_workload,
                "is_available": agent.is_available_for_work,
                "success_rate": agent.success_rate,
                "skills": agent.skills,
                "children": [],
            }
            by_id[agent.id] = node

        # Build tree
        for agent in all_agents:
            node = by_id[agent.id]
            if agent.manager_id and agent.manager_id in by_id:
                by_id[agent.manager_id]["children"].append(node)
            else:
                roots.append(node)

        return roots