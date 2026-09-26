"""
Incident service layer.

Thin, read-focused: incident CREATION/grouping is owned by
`CorrelationEngine` (invoked from `AlertService`), so this class exposes
only the query/lookup and operator-resolve paths the API needs - same
"service owns business rules, repository owns SQL" split as every other
service in this codebase.
"""
from __future__ import annotations

import uuid

from app.models.incident import Incident
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository


class IncidentNotFound(Exception):
    pass


class IncidentService:
    def __init__(self, incident_repo: IncidentRepository, alert_repo: AlertRepository):
        self.incident_repo = incident_repo
        self.alert_repo = alert_repo

    async def get(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.incident_repo.get(incident_id, tenant_id)
        if incident is None:
            raise IncidentNotFound(f"Incident {incident_id} not found")
        return incident

    async def list_incidents(self, tenant_id: uuid.UUID, **filters) -> list[Incident]:
        return await self.incident_repo.list(tenant_id, **filters)

    async def get_with_alerts(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[Incident, list]:
        """Incident Details API - the incident plus every member alert
        (including suppressed ones - the detail view is where a suppressed
        child alert's context matters, unlike the default Active Alerts
        list)."""
        incident = await self.get(incident_id, tenant_id)
        alerts = await self.alert_repo.list(tenant_id, incident_id=incident_id, status=None, limit=1000)
        return incident, alerts

    async def resolve(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.resolve(incident)

    async def acknowledge(self, incident_id: uuid.UUID, tenant_id: uuid.UUID, acknowledged_by: uuid.UUID | None = None) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.acknowledge(incident, acknowledged_by)

    async def investigate(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.investigate(incident)

    async def monitor(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.monitor(incident)

    async def recover(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.recover(incident)

    async def close(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.close(incident)

    async def archive(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident:
        incident = await self.get(incident_id, tenant_id)
        return await self.incident_repo.archive(incident)
