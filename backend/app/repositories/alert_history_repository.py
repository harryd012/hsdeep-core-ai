"""
Repository Pattern: all direct SQLAlchemy access for AlertHistory lives
here. Append-only - there is no update()/delete(), only record() and read
paths, same convention as MetricRepository/EventRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_history import AlertHistory


class AlertHistoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        alert_id: uuid.UUID,
        event_type: str,
        message: str,
        occurred_at: datetime,
        details: dict | None = None,
    ) -> AlertHistory:
        entry = AlertHistory(
            tenant_id=tenant_id,
            alert_id=alert_id,
            event_type=event_type,
            message=message,
            occurred_at=occurred_at,
            details=details if details is not None else {},
        )
        self.db.add(entry)
        await self.db.flush()
        await self.db.refresh(entry)
        return entry

    async def list_for_alert(self, alert_id: uuid.UUID, *, limit: int = 100) -> list[AlertHistory]:
        """Alert Timeline API - full lifecycle history for one alert,
        oldest first (natural reading order for a timeline)."""
        stmt = (
            select(AlertHistory)
            .where(AlertHistory.alert_id == alert_id)
            .order_by(AlertHistory.occurred_at.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
