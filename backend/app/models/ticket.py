"""
Enterprise Ticket Engine models.

The Ticket is the universal ITSM entity that bridges our internal
Incidents and external ITSM provider tickets (ServiceNow, Jira, etc.).
It implements the full enterprise ticket lifecycle:

    Alert → Incident → AI Analysis → Ticket → Assignment → Approval
    → Execution → Validation → Closure → Knowledge Article

`Ticket` - The universal ticket entity. One Ticket can be linked to one
internal Incident and zero or more external ITSM provider tickets via
`TicketLink`. The ticket carries its own lifecycle (open → assigned →
in_progress → pending_approval → resolved → closed → reopened), SLA
tracking, assignment, and AI-generated content.

`TicketLink` - The bidirectional-sync mapping row: one per
(Ticket) ↔ (external ITSM ticket) pair. Same "correlation_id +
last_pushed_hash + last_pulled_hash" conflict resolution design as the
existing `ServiceNowIncidentLink`, but provider-agnostic.

`TicketComment` - Work notes and comments on a ticket. Distinguishes
internal work notes from public comments (same as ServiceNow's
work_notes vs. comments distinction).

`TicketAttachment` - File attachments on a ticket. Metadata only - the
actual file content is stored in object storage (S3/MinIO), with the
storage key referenced here.

`TicketHistory` - The audit trail of every state change, assignment
change, priority change, etc. on a ticket. Same "every transition
recorded" pattern as Incident's lifecycle timestamps, but as a
queryable child table for full timeline reconstruction.

These tables are ADDITIVE - they do not duplicate the existing Incident
model. A Ticket references an Incident via FK but the Incident model
itself is unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.assignment_group import AssignmentGroup
    from app.models.incident import Incident
    from app.models.itsm import ITSMConnection
    from app.models.sla import SLAPolicy
    from app.models.tenant import Tenant
    from app.models.user import User


class Ticket(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """The universal enterprise ticket entity.

    Bridges internal Incidents and external ITSM provider tickets. A
    Ticket can exist without an Incident (e.g. a ticket created directly
    in ServiceNow and pulled in), and an Incident can exist without a
    Ticket (not every incident needs an ITSM ticket).

    Lifecycle: open → assigned → in_progress → pending_approval →
    resolved → closed → (reopened → ... → closed).

    The `source` field indicates whether this ticket originated from
    our system (incident_triggered) or was pulled from an external
    provider (external_import).
    """

    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ticket_number", name="uq_tickets_tenant_id_ticket_number"),
        # Knowledge-link lookup path; declared here so the model is the honest
        # source of truth.
        Index("ix_tickets_knowledge_article_id", "knowledge_article_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )

    ticket_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="incident_triggered", server_default="incident_triggered"
    )

    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="open", server_default="open", index=True
    )

    priority: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)

    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    subcategory: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Assignment
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_team: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    assignment_group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assignment_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # SLA tracking (legacy)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_breached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )

    # Enterprise SLA Engine columns
    sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sla_response_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_resolution_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_breached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_warning_sent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    sla_paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_paused_time_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # Lifecycle timestamps
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    in_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_approval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Escalation tracking
    escalation_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # AI-generated content
    ai_ticket_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_executive_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_engineer_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_customer_update: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_playbook_selection: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Integer, not float: the column is INTEGER (migration c4d9a2b7e105
    # converted it from DOUBLE PRECISION after verifying no fractional rows).
    # The annotation previously said float while the column said Integer.
    ai_execution_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_automation_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)

    knowledge_article_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_articles.id", ondelete="SET NULL"), nullable=True
    )

    watchers: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    custom_fields: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    tenant: Mapped["Tenant"] = relationship()
    incident: Mapped["Incident | None"] = relationship()
    assignment_group_rel: Mapped["AssignmentGroup | None"] = relationship(
        foreign_keys="Ticket.assigned_group_id"
    )
    sla_policy: Mapped["SLAPolicy | None"] = relationship(
        "app.models.sla.SLAPolicy",
        foreign_keys="Ticket.sla_policy_id",
    )
    links: Mapped[list["TicketLink"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    comments: Mapped[list["TicketComment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["TicketAttachment"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )
    history: Mapped[list["TicketHistory"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Ticket id={self.id} ticket_number={self.ticket_number!r} status={self.status!r} priority={self.priority!r}>"


class TicketLink(Base, UUIDPKMixin, TimestampMixin):
    """Bidirectional-sync mapping between a Ticket and an external ITSM
    provider's ticket.
    """

    __tablename__ = "ticket_links"
    __table_args__ = (
        UniqueConstraint("connection_id", "ticket_id", name="uq_ticket_links_connection_id_ticket_id"),
        UniqueConstraint("connection_id", "external_id", name="uq_ticket_links_connection_id_external_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("itsm_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    external_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    sync_direction: Mapped[str] = mapped_column(
        String(30), nullable=False, default="bidirectional", server_default="bidirectional"
    )

    last_pushed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_pulled_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    has_unresolved_conflict: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    conflict_details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="links")

    def __repr__(self) -> str:
        return f"<TicketLink id={self.id} ticket_id={self.ticket_id} external_id={self.external_id!r}>"


class TicketComment(Base, UUIDPKMixin, TimestampMixin):
    """A comment or work note on a ticket."""

    __tablename__ = "ticket_comments"
    __table_args__ = (
        # Author-lookup phrase search path; declared here so the model is the
        # honest source of truth.
        Index("ix_ticket_comments_author_id", "author_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    body: Mapped[str] = mapped_column(Text, nullable=False)

    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_synced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    external_comment_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="comments")

    def __repr__(self) -> str:
        return f"<TicketComment id={self.id} ticket_id={self.ticket_id} is_internal={self.is_internal}>"


class TicketAttachment(Base, UUIDPKMixin, TimestampMixin):
    """A file attachment on a ticket. Metadata only."""

    __tablename__ = "ticket_attachments"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)

    is_synced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    external_attachment_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="attachments")

    def __repr__(self) -> str:
        return f"<TicketAttachment id={self.id} ticket_id={self.ticket_id} filename={self.filename!r}>"


class TicketHistory(Base, UUIDPKMixin, TimestampMixin):
    """The audit trail of every state change on a ticket."""

    __tablename__ = "ticket_history"
    __table_args__ = (
        # "Who changed what" audit query path; declared here so the model is
        # the honest source of truth.
        Index("ix_ticket_history_actor_id", "actor_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    action_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    field_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    ticket: Mapped["Ticket"] = relationship(back_populates="history")

    def __repr__(self) -> str:
        return f"<TicketHistory id={self.id} ticket_id={self.ticket_id} action_type={self.action_type!r}>"