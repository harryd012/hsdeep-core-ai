"""
Outbound email queue. Requests enqueue a row; the worker's delivery job
(app/scheduler/email_jobs.py) sends it, so an SMTP outage never fails a
request and every retry and failure is visible.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class EmailOutbox(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "email_outbox"
    __table_args__ = (Index("ix_email_outbox_status_next_attempt", "status", "next_attempt_at"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    # What the message is for (password_reset, email_verify, invite) - for
    # operators reading the queue; never contains the token.
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    # queued | sent | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
