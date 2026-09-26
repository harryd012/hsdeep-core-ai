"""
SOC Analyst service layer.

SOC analysts are Agent rows (department="soc", role="soc_analyst") — NOT a
separate ORM model. This service wraps AgentRepository +
NocEventRepository to produce the SOC roster with live NOC assignment
counts, and to resolve the per-analyst detail view.

Same "service owns business rules, repository owns SQL" split as every
other service in this codebase.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.agent import Agent
from app.repositories.agent_repository import AgentRepository
from app.repositories.noc_event_repository import NocEventRepository


class SocAnalystNotFound(Exception):
    pass


class SocService:
    def __init__(
        self,
        agent_repo: AgentRepository,
        noc_repo: NocEventRepository,
    ):
        self.agent_repo = agent_repo
        self.noc_repo = noc_repo

    async def _get_soc_analyst(
        self, analyst_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> Agent:
        """Get an agent and validate it is a SOC analyst."""
        agent = await self.agent_repo.get(analyst_id, tenant_id)
        if agent is None:
            raise SocAnalystNotFound(f"Analyst {analyst_id} not found")
        if agent.department != "soc":
            raise SocAnalystNotFound(
                f"Agent {analyst_id} is not a SOC analyst (department={agent.department!r})"
            )
        return agent

    async def list_analysts(
        self,
        tenant_id: uuid.UUID,
        *,
        availability: str | None = None,
        level: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Agent]:
        """List SOC analysts with optional filters."""
        return await self.agent_repo.list(
            tenant_id,
            department="soc",
            role="soc_analyst",
            availability=availability,
            level=level,
            limit=limit,
            offset=offset,
        )

    async def get_analyst_detail(
        self, analyst_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> tuple[Agent, list]:
        """Get a SOC analyst with their assigned NOC events."""
        agent = await self._get_soc_analyst(analyst_id, tenant_id)
        events = await self.noc_repo.list_for_analyst(
            tenant_id, analyst_id, limit=100
        )
        return agent, events

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        """Aggregate counts for the SOC roster header."""
        analysts = await self.agent_repo.list(
            tenant_id, department="soc", role="soc_analyst", limit=200
        )
        event_counts = await self.noc_repo.count_by_analyst(tenant_id)

        total = len(analysts)
        available = sum(1 for a in analysts if a.availability == "available")
        busy = sum(1 for a in analysts if a.availability == "busy")
        offline = sum(1 for a in analysts if a.availability not in ("available", "busy"))

        total_open = sum(
            c.get("open", 0) + c.get("acknowledged", 0)
            for c in event_counts.values()
        )
        unassigned = await self.noc_repo.count(
            tenant_id, status="open"
        ) - sum(
            c.get("open", 0) + c.get("acknowledged", 0)
            for c in event_counts.values()
        )

        avg_success = (
            sum(a.success_rate for a in analysts) / len(analysts)
            if analysts else 0.0
        )

        return {
            "total_analysts": total,
            "available_analysts": available,
            "busy_analysts": busy,
            "offline_analysts": offline,
            "total_open_events": total_open,
            "unassigned_open_events": max(0, unassigned),
            "avg_success_rate": round(avg_success, 1),
        }

    async def assign_event_to_analyst(
        self,
        tenant_id: uuid.UUID,
        noc_event_id: uuid.UUID,
        analyst_id: uuid.UUID,
    ) -> None:
        """Assign a NOC event to a SOC analyst (validates both exist)."""
        await self._get_soc_analyst(analyst_id, tenant_id)
        from app.services.noc_service import NocEventService
        noc_service = NocEventService(
            noc_repo=self.noc_repo,
            agent_repo=self.agent_repo,
        )
        await noc_service.assign_analyst(noc_event_id, tenant_id, analyst_id)
