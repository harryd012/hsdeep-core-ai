"""
Repository for Event persistence. Events are append-only (see the model's
docstring) - there is no update-in-place path for platform-generated events.
For vendor-sourced events, `(monitoring_source_id, external_id)` acts as the
natural identity key (per the model's partial unique index) so re-syncing
the same vendor event is idempotent instead of duplicating rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event


class EventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_external_id(
        self, monitoring_source_id: uuid.UUID, external_id: str
    ) -> Event | None:
        stmt = select(Event).where(
            Event.monitoring_source_id == monitoring_source_id,
            Event.external_id == external_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        tenant_id: uuid.UUID,
        event_type: str,
        message: str,
        occurred_at: datetime,
        monitoring_source_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        external_id: str | None = None,
        severity: str = "info",
        raw_payload: dict | None = None,
    ) -> tuple[Event, bool]:
        """Create or refresh an event from a collector reading.

        Returns (event, created) so callers can report accurate
        created/updated counts back to the caller.

        Identity is only resolvable when both `monitoring_source_id` and
        `external_id` are present (per the model's partial unique index).
        Platform-generated events (no `external_id`) have no natural
        identity key, so they are always inserted as new rows - this is the
        "record" path referred to by the design, reached through the same
        method as the vendor "upsert" path.
        """
        payload = raw_payload if raw_payload is not None else {}

        existing = None
        if external_id is not None and monitoring_source_id is not None:
            existing = await self.get_by_external_id(monitoring_source_id, external_id)

        if existing is None:
            event = Event(
                tenant_id=tenant_id,
                device_id=device_id,
                monitoring_source_id=monitoring_source_id,
                external_id=external_id,
                event_type=event_type,
                severity=severity,
                message=message,
                occurred_at=occurred_at,
                raw_payload=payload,
            )
            self.db.add(event)
            await self.db.flush()
            await self.db.refresh(event)
            return event, True

        existing.device_id = device_id
        existing.event_type = event_type
        existing.severity = severity
        existing.message = message
        existing.occurred_at = occurred_at
        existing.raw_payload = payload
        await self.db.flush()
        await self.db.refresh(existing)
        return existing, False

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Event]:
        stmt = select(Event).where(Event.tenant_id == tenant_id)
        if device_id is not None:
            stmt = stmt.where(Event.device_id == device_id)
        if severity is not None:
            stmt = stmt.where(Event.severity == severity)
        if event_type is not None:
            stmt = stmt.where(Event.event_type == event_type)
        if since is not None:
            stmt = stmt.where(Event.occurred_at >= since)
        stmt = stmt.order_by(Event.occurred_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        event_type: str | None = None,
        since: datetime | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Event).where(Event.tenant_id == tenant_id)
        if device_id is not None:
            stmt = stmt.where(Event.device_id == device_id)
        if severity is not None:
            stmt = stmt.where(Event.severity == severity)
        if event_type is not None:
            stmt = stmt.where(Event.event_type == event_type)
        if since is not None:
            stmt = stmt.where(Event.occurred_at >= since)
        result = await self.db.execute(stmt)
        return result.scalar_one()
