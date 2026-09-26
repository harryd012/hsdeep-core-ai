"""
Repository Pattern: direct SQLAlchemy access for ProcessedStripeEvent rows.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.processed_stripe_event import ProcessedStripeEvent


class ProcessedStripeEventRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def exists(self, event_id: str) -> bool:
        stmt = select(ProcessedStripeEvent.event_id).where(
            ProcessedStripeEvent.event_id == event_id
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def create(self, event_id: str) -> ProcessedStripeEvent:
        row = ProcessedStripeEvent(event_id=event_id)
        self.db.add(row)
        await self.db.flush()
        return row