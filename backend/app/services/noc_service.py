"""
NOC Event service layer.

Owns the business rules that turn operational signals (Alert status changes,
webhook triggers, probe results) into NOC events: when to open one, when a
recurring signal should bump an existing event's occurrence_count instead
of creating noise, and when to auto-resolve. Same "service owns business
rules, repository owns SQL" split as AlertService.

SOC analyst assignment is also owned here — the service wraps
AgentService to resolve department="soc" analysts and attaches live
assignment counts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.noc_event import NocEvent
from app.repositories.noc_event_repository import NocEventRepository
from app.repositories.agent_repository import AgentRepository


class NocEventNotFound(Exception):
    pass


class NocEventInvalidTransition(Exception):
    pass


class NocEventService:
    def __init__(
        self,
        noc_repo: NocEventRepository,
        agent_repo: AgentRepository,
    ):
        self.noc_repo = noc_repo
        self.agent_repo = agent_repo

    # -- Reads --

    async def get(self, event_id: uuid.UUID, tenant_id: uuid.UUID) -> NocEvent:
        event = await self.noc_repo.get(event_id, tenant_id)
        if event is None:
            raise NocEventNotFound(f"NOC event {event_id} not found")
        return event

    async def get_with_joins(
        self, event_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> NocEvent:
        event = await self.noc_repo.get_with_joins(event_id, tenant_id)
        if event is None:
            raise NocEventNotFound(f"NOC event {event_id} not found")
        return event

    async def list_events(
        self, tenant_id: uuid.UUID, **filters
    ) -> list[NocEvent]:
        return await self.noc_repo.list(tenant_id, **filters)

    async def count_events(self, tenant_id: uuid.UUID, **filters) -> int:
        return await self.noc_repo.count(tenant_id, **filters)

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        return await self.noc_repo.summary(tenant_id)

    async def statistics(self, tenant_id: uuid.UUID) -> dict:
        return await self.noc_repo.statistics(tenant_id)

    # -- Create --

    async def create(
        self,
        tenant_id: uuid.UUID,
        *,
        title: str,
        severity: str,
        source: str,
        category: str = "other",
        description: str | None = None,
        event_signature: str | None = None,
        triggered_at: datetime | None = None,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        source_agent_id: uuid.UUID | None = None,
        assigned_analyst_id: uuid.UUID | None = None,
        confidence_score: float | None = None,
    ) -> NocEvent:
        """Create a new NOC event.

        Validates that assigned_analyst_id (if provided) points to a valid
        SOC analyst (department="soc").
        """
        # Validate assigned analyst if provided
        if assigned_analyst_id is not None:
            analyst = await self.agent_repo.get(assigned_analyst_id, tenant_id)
            if analyst is None:
                raise NocEventNotFound(f"Analyst {assigned_analyst_id} not found")
            if analyst.department != "soc":
                raise ValueError(
                    f"Agent {assigned_analyst_id} is not a SOC analyst (department={analyst.department!r})"
                )

        now = datetime.now(timezone.utc)
        event = await self.noc_repo.create(
            tenant_id=tenant_id,
            title=title,
            severity=severity,
            source=source,
            category=category,
            description=description,
            event_signature=event_signature,
            triggered_at=triggered_at or now,
            last_occurred_at=triggered_at or now,
            site_id=site_id,
            device_id=device_id,
            source_agent_id=source_agent_id,
            assigned_analyst_id=assigned_analyst_id,
            confidence_score=confidence_score,
            status="open",
            occurrence_count=1,
        )
        return event

    # -- Update --

    async def update(
        self,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        assigned_analyst_id: uuid.UUID | None = None,
        status: str | None = None,
    ) -> NocEvent:
        """Update an existing NOC event.

        Validates status transitions:
          - open -> acknowledged -> resolved
          - acknowledged -> resolved
          - resolved is terminal (no transitions out)
        """
        event = await self.get(event_id, tenant_id)

        # Validate status transition
        if status is not None and status != event.status:
            self._validate_status_transition(event.status, status)

        # Validate assigned analyst if changing
        if assigned_analyst_id is not None and assigned_analyst_id != event.assigned_analyst_id:
            analyst = await self.agent_repo.get(assigned_analyst_id, tenant_id)
            if analyst is None:
                raise NocEventNotFound(f"Analyst {assigned_analyst_id} not found")
            if analyst.department != "soc":
                raise ValueError(
                    f"Agent {assigned_analyst_id} is not a SOC analyst (department={analyst.department!r})"
                )

        update_fields: dict = {}
        if title is not None:
            update_fields["title"] = title
        if description is not None:
            update_fields["description"] = description
        if severity is not None:
            update_fields["severity"] = severity
        if category is not None:
            update_fields["category"] = category
        if assigned_analyst_id is not None:
            update_fields["assigned_analyst_id"] = assigned_analyst_id
        if status is not None:
            update_fields["status"] = status
            now = datetime.now(timezone.utc)
            if status == "acknowledged":
                update_fields["acknowledged_at"] = now
            elif status == "resolved":
                update_fields["resolved_at"] = now

        if update_fields:
            event = await self.noc_repo.update(event, **update_fields)

        return event

    def _validate_status_transition(self, current: str, target: str) -> None:
        """Validate a status transition follows the lifecycle rules.

        Allowed transitions:
          - open -> acknowledged
          - open -> resolved (direct resolve)
          - acknowledged -> resolved
          - resolved is terminal (no outbound transitions)
        """
        allowed = {
            "open": {"acknowledged", "resolved"},
            "acknowledged": {"resolved"},
            "resolved": set(),  # terminal state
        }
        if target not in allowed.get(current, set()):
            raise NocEventInvalidTransition(
                f"Invalid status transition: {current!r} -> {target!r}"
            )

    # -- Operator actions --

    async def acknowledge(
        self,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID,
        acknowledged_by: uuid.UUID,
    ) -> NocEvent:
        event = await self.get(event_id, tenant_id)
        self._validate_status_transition(event.status, "acknowledged")
        return await self.noc_repo.acknowledge(
            event, acknowledged_by=acknowledged_by
        )

    async def resolve(
        self,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID,
        resolved_by: uuid.UUID | None = None,
    ) -> NocEvent:
        event = await self.get(event_id, tenant_id)
        self._validate_status_transition(event.status, "resolved")
        return await self.noc_repo.resolve(event, resolved_by=resolved_by)

    async def assign_analyst(
        self,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID,
        analyst_id: uuid.UUID,
    ) -> NocEvent:
        """Assign a NOC event to a SOC analyst. Validates the analyst exists
        and belongs to the SOC department."""
        event = await self.get(event_id, tenant_id)
        analyst = await self.agent_repo.get(analyst_id, tenant_id)
        if analyst is None:
            raise NocEventNotFound(f"Analyst {analyst_id} not found")
        if analyst.department != "soc":
            raise ValueError(
                f"Agent {analyst_id} is not a SOC analyst (department={analyst.department!r})"
            )
        return await self.noc_repo.assign_analyst(event, analyst_id)
