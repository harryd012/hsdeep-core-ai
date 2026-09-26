"""
Processed Stripe Event model.

Tracks which Stripe webhook event IDs have already been processed so that
Stripe's automatic retries (any non-2xx or timeout) never double-apply an
event. The event_id is the natural primary key - Stripe event IDs are
globally unique and immutable.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProcessedStripeEvent(Base):
    __tablename__ = "processed_stripe_events"

    # Stripe event id (e.g. "evt_...") - globally unique, immutable.
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    # When the event was first successfully processed.
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<ProcessedStripeEvent event_id={self.event_id!r}>"