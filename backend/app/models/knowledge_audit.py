"""
Knowledge Base and Audit Event models.

KnowledgeArticle - knowledge base articles from resolved tickets.
AuditEvent - unified audit trail for the entire platform.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class KnowledgeArticle(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "knowledge_articles"
    __table_args__ = (
        # Query-path index added by the knowledge-engine migration; declared
        # here so the model is the honest source of truth.
        Index("ix_knowledge_articles_author_id", "author_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    article_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_steps: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    prevention: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft", index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    helpful_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<KnowledgeArticle id={self.id} article_number={self.article_number!r} status={self.status!r}>"


class AuditEvent(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "audit_events"
    __table_args__ = (
        # Time-ordered audit query path; declared here so the model is the
        # honest source of truth.
        Index("ix_audit_events_created_at", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default="user"
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    new_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    def __repr__(self) -> str:
        return f"<AuditEvent id={self.id} action={self.action!r} entity_type={self.entity_type!r}>"