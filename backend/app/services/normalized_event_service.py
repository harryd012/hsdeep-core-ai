"""
Normalized Event service layer.

Owns the business rules for the telemetry plane:
- Ingesting normalized events from integrations
- Deduplication (flapping detection)
- Correlation grouping
- Generating NocEvents from correlated normalized events
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.normalized_event import NormalizedEvent
from app.repositories.normalized_event_repository import NormalizedEventRepository


class NormalizedEventNotFound(Exception):
    pass


class NormalizedEventService:
    def __init__(self, repo: NormalizedEventRepository):
        self.repo = repo

    async def get(self, event_id: uuid.UUID, tenant_id: uuid.UUID) -> NormalizedEvent:
        event = await self.repo.get(event_id, tenant_id)
        if event is None:
            raise NormalizedEventNotFound(f"Normalized event {event_id} not found")
        return event

    async def get_with_joins(
        self, event_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> NormalizedEvent:
        event = await self.repo.get_with_joins(event_id, tenant_id)
        if event is None:
            raise NormalizedEventNotFound(f"Normalized event {event_id} not found")
        return event

    async def list_events(
        self, tenant_id: uuid.UUID, **filters
    ) -> list[NormalizedEvent]:
        return await self.repo.list(tenant_id, **filters)

    async def count_events(self, tenant_id: uuid.UUID, **filters) -> int:
        return await self.repo.count(tenant_id, **filters)

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        return await self.repo.summary(tenant_id)

    async def ingest(
        self,
        tenant_id: uuid.UUID,
        *,
        title: str,
        severity: str,
        event_category: str,
        event_type: str,
        telemetry_type: str,
        event_timestamp: datetime,
        integration_id: uuid.UUID | None = None,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        source_event_id: str | None = None,
        event_signature: str | None = None,
        description: str | None = None,
        attributes: dict | None = None,
        confidence_score: float | None = None,
        raw_reference: str | None = None,
    ) -> tuple[NormalizedEvent, bool]:
        """Ingest a normalized event.

        Returns (event, created) where created is True if this was a new event
        or False if it was a duplicate (occurrence_count bumped).

        Deduplication logic:
        - If an active (non-suppressed, non-deleted) event exists with the same
          integration_id + event_signature, bump its occurrence_count instead
          of creating a new row.
        """
        # Check for existing active event with same signature
        if event_signature and integration_id:
            existing = await self._find_active_by_signature(
                tenant_id, integration_id, event_signature
            )
            if existing is not None:
                # Duplicate — bump occurrence
                await self.repo.bump_occurrence(existing)
                return existing, False

        # Create new event
        event = await self.repo.create(
            tenant_id=tenant_id,
            integration_id=integration_id,
            site_id=site_id,
            device_id=device_id,
            source_event_id=source_event_id,
            event_signature=event_signature,
            event_category=event_category,
            event_type=event_type,
            severity=severity,
            title=title,
            description=description,
            attributes=attributes or {},
            event_timestamp=event_timestamp,
            telemetry_type=telemetry_type,
            confidence_score=confidence_score,
            raw_reference=raw_reference,
            occurrence_count=1,
            last_occurred_at=event_timestamp,
        )
        return event, True

    async def _find_active_by_signature(
        self,
        tenant_id: uuid.UUID,
        integration_id: uuid.UUID,
        event_signature: str,
    ) -> NormalizedEvent | None:
        """Find an active (non-suppressed, non-deleted) event by signature."""
        events = await self.repo.list(
            tenant_id,
            integration_id=integration_id,
            is_suppressed=False,
            limit=10,  # Check recent events for matching signature
        )
        for event in events:
            if event.event_signature == event_signature:
                return event
        return None

    async def update(
        self,
        event_id: uuid.UUID,
        tenant_id: uuid.UUID,
        *,
        title: str | None = None,
        description: str | None = None,
        severity: str | None = None,
        event_category: str | None = None,
        event_type: str | None = None,
        is_suppressed: bool | None = None,
        attributes: dict | None = None,
    ) -> NormalizedEvent:
        event = await self.get(event_id, tenant_id)
        update_fields: dict = {}
        if title is not None:
            update_fields["title"] = title
        if description is not None:
            update_fields["description"] = description
        if severity is not None:
            update_fields["severity"] = severity
        if event_category is not None:
            update_fields["event_category"] = event_category
        if event_type is not None:
            update_fields["event_type"] = event_type
        if is_suppressed is not None:
            update_fields["is_suppressed"] = is_suppressed
        if attributes is not None:
            update_fields["attributes"] = attributes
        if update_fields:
            event = await self.repo.update(event, **update_fields)
        return event

    async def suppress(self, event_id: uuid.UUID, tenant_id: uuid.UUID) -> NormalizedEvent:
        """Suppress this event (symptom of a root cause)."""
        event = await self.get(event_id, tenant_id)
        return await self.repo.suppress(event)

    async def link_to_noc_event(
        self, event_id: uuid.UUID, tenant_id: uuid.UUID, noc_event_id: uuid.UUID
    ) -> NormalizedEvent:
        """Link this normalized event to a NocEvent."""
        event = await self.get(event_id, tenant_id)
        return await self.repo.link_to_noc_event(event, noc_event_id)
