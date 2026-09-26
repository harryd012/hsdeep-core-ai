"""
Task Routing Engine — routes tasks to the best available agent.

Routing strategy:
1. Match by task type (agent.supported_task_types)
2. Match by required skills (agent.skills overlap)
3. Filter by availability and workload capacity
4. Score by workload (least loaded first) and success rate
5. Return ranked candidates
"""
from __future__ import annotations

import uuid

from app.models.agent import Agent
from app.models.task import Task
from app.repositories.agent_repository import AgentRepository


class NoAgentAvailable(Exception):
    pass


class RoutingEngine:
    def __init__(self, agent_repo: AgentRepository):
        self.agent_repo = agent_repo

    async def find_candidates(
        self,
        tenant_id: uuid.UUID,
        task: Task,
        *,
        department: str | None = None,
        level: str | None = None,
        limit: int = 10,
    ) -> list[Agent]:
        """Find candidate agents for a task, ranked by suitability."""
        # Start with task-type matching
        candidates = await self.agent_repo.list_by_task_type(
            tenant_id, task.task_type, limit=limit * 3
        )

        # If no task-type matches, fall back to skill matching
        if not candidates and task.required_skills:
            candidates = await self.agent_repo.list_by_skills(
                tenant_id,
                task.required_skills,
                department=department,
                level=level,
                limit=limit * 3,
            )

        # If still no matches, fall back to available agents
        if not candidates:
            candidates = await self.agent_repo.list_available(
                tenant_id, department=department, level=level, limit=limit * 3
            )

        # Filter by availability and workload
        eligible = [
            a for a in candidates
            if a.is_available_for_work and a.can_accept_task
        ]

        # Score and rank
        scored = [(self._score_agent(a, task), a) for a in eligible]
        scored.sort(key=lambda x: x[0], reverse=True)

        return [agent for _, agent in scored[:limit]]

    async def route(
        self,
        tenant_id: uuid.UUID,
        task: Task,
        *,
        department: str | None = None,
        level: str | None = None,
    ) -> Agent:
        """Route a task to the best available agent."""
        candidates = await self.find_candidates(
            tenant_id, task, department=department, level=level, limit=1
        )

        if not candidates:
            raise NoAgentAvailable(
                f"No available agent found for task type '{task.task_type}' "
                f"with skills {task.required_skills}"
            )

        return candidates[0]

    async def route_to_department(
        self,
        tenant_id: uuid.UUID,
        task: Task,
        department: str,
    ) -> Agent:
        """Route a task to the best agent in a specific department."""
        return await self.route(tenant_id, task, department=department)

    async def route_to_level(
        self,
        tenant_id: uuid.UUID,
        task: Task,
        level: str,
    ) -> Agent:
        """Route a task to the best agent at a specific level."""
        return await self.route(tenant_id, task, level=level)

    @staticmethod
    def _score_agent(agent: Agent, task: Task) -> float:
        """Score an agent's suitability for a task (higher is better)."""
        score = 0.0

        # Workload capacity (0-40 points): prefer less loaded agents
        if agent.max_workload > 0:
            utilization = agent.current_workload / agent.max_workload
            score += (1 - utilization) * 40

        # Success rate (0-30 points): prefer higher success rate
        score += (agent.success_rate / 100) * 30

        # Skill match (0-20 points): more matching skills = better
        if task.required_skills and agent.skills:
            matching = len(set(task.required_skills) & set(agent.skills))
            total = len(task.required_skills)
            if total > 0:
                score += (matching / total) * 20

        # Task type match (0-10 points): bonus for explicit support
        if task.task_type in (agent.supported_task_types or []):
            score += 10

        return round(score, 2)