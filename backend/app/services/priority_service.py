"""
Priority service layer.

Thin, mirrors `IncidentService`'s split: `PriorityEngine.recompute()` owns
the actual scoring math (invoked automatically from `AlertService` after
correlation, same as `CorrelationEngine`), so this class exposes only the
query/lookup, manual-recompute-on-demand, and manual-override paths the
API needs.
"""
from __future__ import annotations

import uuid

from app.models.incident import Incident
from app.repositories.incident_repository import IncidentRepository
from app.services.priority.priority_engine import PriorityEngine


class IncidentNotFoundForPriority(Exception):
    pass


class PriorityService:
    def __init__(self, incident_repo: IncidentRepository, priority_engine: PriorityEngine):
        self.incident_repo = incident_repo
        self.priority_engine = priority_engine

    async def get(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.incident_repo.get(incident_id, tenant_id)
        if incident is None:
            raise IncidentNotFoundForPriority(f"Incident {incident_id} not found")
        return incident

    async def recompute(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        """Manual on-demand recompute (e.g. after an operator edits a
        Device's criticality or BusinessService mapping and wants the
        incident's priority to reflect it immediately, rather than waiting
        for the next alert to trigger AlertService's automatic recompute)."""
        incident = await self.get(incident_id, tenant_id)
        return await self.priority_engine.recompute(incident)

    async def list_prioritized(self, tenant_id: uuid.UUID, **filters) -> list[Incident]:
        return await self.incident_repo.list_prioritized(tenant_id, **filters)

    async def priority_distribution(self, tenant_id: uuid.UUID) -> dict[str, int]:
        return await self.incident_repo.priority_distribution(tenant_id)

    async def set_manual_override(
        self, incident_id: uuid.UUID, tenant_id: uuid.UUID, *, priority: str | None, overridden_by: uuid.UUID | None
    ) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.set_manual_override(incident, priority=priority, overridden_by=overridden_by)
