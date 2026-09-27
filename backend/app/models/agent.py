"""
Agent Registry model — Virtual Company Multi-Agent Organization.

Data-driven agent profiles representing the AI organizational hierarchy.
Agents are logical personas sharing an AI infrastructure layer — NOT separate
LLM instances. Critical for RTX 3060 12GB + 32GB RAM constraint.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Agent(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """A logical AI agent persona in the virtual company organization."""

    __tablename__ = "agents"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Identity --
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    department: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(10), nullable=False, index=True)

    # -- Hierarchy --
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reports: Mapped[list["Agent"]] = relationship(
        back_populates="manager", foreign_keys=[manager_id]
    )
    # -- Permissions & Authority --
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list, server_default="{}"
    )
    escalation_authority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none", server_default="none"
    )
    can_approve: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    approval_limit: Mapped[str] = mapped_column(
        String(20), nullable=False, default="low", server_default="low"
    )

    # -- Workload & Status --
    max_workload: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    current_workload: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    availability: Mapped[str] = mapped_column(
        String(20), nullable=False, default="available", server_default="available"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )

    # -- Performance --
    tasks_completed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    tasks_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    avg_resolution_minutes: Mapped[float | None] = mapped_column(Integer, nullable=True)
    performance_score: Mapped[float] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )

    # -- Assignments --
    assigned_customers: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )
    assigned_projects: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )

    # -- Metadata --
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # -- Descriptive / presentation attributes already published by the API --
    # app/schemas/agent.py has declared these on AgentBase (create + read) and
    # AgentUpdate, but the model never implemented them, so the published
    # contract was unimplementable: Agent(**payload) raised TypeError on
    # `description` and AgentRead could not serialize them. Additive, nullable
    # (config defaulted) so existing rows and callers are unaffected.
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )

    tenant: Mapped["Tenant"] = relationship()

    def __repr__(self) -> str:
        return f"<Agent id={self.id} agent_id={self.agent_id!r} role={self.role!r} level={self.level!r}>"

    @property
    def is_available_for_work(self) -> bool:
        """Check if agent can accept new tasks."""
        return (
            self.is_active
            and self.status == "active"
            and self.availability in ("available", "busy")
            and self.current_workload < self.max_workload
        )

    @property
    def can_accept_task(self) -> bool:
        """Check if agent can accept a new task (alias for routing engine)."""
        return self.is_available_for_work

    @property
    def success_rate(self) -> float:
        """Calculate task success rate."""
        total = self.tasks_completed + self.tasks_failed
        if total == 0:
            return 0.0
        return (self.tasks_completed / total) * 100.0

    manager: Mapped["Agent | None"] = relationship(
        back_populates="reports", remote_side="Agent.id", foreign_keys=[manager_id]
    )

    # -- Capabilities --
    skills: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )
    supported_task_types: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list, server_default="{}"
    )
    responsibilities: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, default=list, server_default="{}"
    )