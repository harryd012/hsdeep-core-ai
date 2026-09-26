"""
Priority-to-ServiceNow Bridge.

Wired into AlertService._evaluate_workflow_triggers() as the priority_changed
path. When a re-scoped incident reaches P1 or P2, checks if an SN link already
exists; if not, auto-creates one via SyncService.
"""
from __future__ import annotations

import uuid

from app.models.incident import Incident
from app.repositories.servicenow_connection_repository import ServiceNowConnectionRepository
from app.services.servicenow.incident_sync import ServiceNowIncidentSyncService


class PriorityToServiceNowBridge:
    """Wired into AlertService._evaluate_workflow_triggers() as the
    priority_changed path. When an incident's new priority qualifies (P1/P2),
    and no SN link exists yet, auto-creates a push job."""

    AUTO_CREATE_PRIORITIES = ("P1", "P2")

    def __init__(self, sync_service: ServiceNowIncidentSyncService):
        self.sync_service = sync_service

    async def evaluate(
        self,
        incident: Incident,
        tenant_id: uuid.UUID,
        new_priority: str,
    ) -> None:
        """If the incident's NEW priority is P1/P2 and no SN link exists,
        push to ServiceNow automatically."""
        if new_priority not in self.AUTO_CREATE_PRIORITIES:
            return

        # Check for existing link
        existing_link = await self.sync_service.link_repo.get_by_incident_id(tenant_id, incident.id)
        if existing_link and existing_link.sn_sys_id:
            return  # Already linked — skip auto-create

        # Find an active connection (take the first/only one)
        connections = await self.sync_service.conn_repo.list_active(tenant_id)
        if not connections:
            return  # No active SN connection

        # Push to ServiceNow
        conn = connections[0]
        await self.sync_service.push_incident_to_servicenow(
            tenant_id=tenant_id,
            incident_id=incident.id,
            connection_id=conn.id,
        )
