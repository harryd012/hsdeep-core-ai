"""
Playbook / SOP Engine models (Phase 5).

Replaces generic text SOPs with structured enterprise playbooks. A
Playbook is a versioned, structured remediation procedure with
execution steps, rollback steps, validation steps, variables, and
platform/vendor support metadata.

This EXTENDS the existing SOP/Runbook models - a Playbook references
an SOPVersion for backward compatibility, but adds the structured
execution, rollback, and validation framework that the existing SOP
model lacks.

`Playbook` - The playbook catalog entry with metadata and matching
criteria. Versioned via `PlaybookVersion`.

`PlaybookVersion` - A specific version of a playbook's content (steps,
rollback, validation, variables). Same versioning pattern as
`SOPVersion`.

`PlaybookStep` - An ordered execution step within a playbook version.
Stored as a child table (not JSONB) so individual steps can be
queried, reordered, and referenced by execution logs.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.sop import SOP
    from app.models.tenant import Tenant
    from app.models.user import User


class Playbook(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """The enterprise playbook catalog entry."""

    __tablename__ = "playbooks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "playbook_number", name="uq_playbooks_tenant_id_playbook_number"),
        # Catalog filter/tag query paths; declared here so the model is the
        # honest source of truth.
        Index("ix_playbooks_owner_id", "owner_id"),
        Index("ix_playbooks_tags", "tags"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    playbook_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    sop_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sops.id", ondelete="SET NULL"), nullable=True, index=True
    )

    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list, server_default="{}"
    )

    business_impact: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default="medium"
    )
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium", server_default="medium"
    )
    approval_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="single", server_default="single"
    )
    execution_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default="manual", index=True
    )

    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    device_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    min_priority: Mapped[str | None] = mapped_column(String(10), nullable=True)

    supported_vendors: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )
    supported_platforms: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=list, server_default="{}"
    )
    dependencies: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    ai_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_versions.id", ondelete="SET NULL"), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship()
    sop: Mapped["SOP | None"] = relationship()
    versions: Mapped[list["PlaybookVersion"]] = relationship(
        back_populates="playbook",
        foreign_keys="PlaybookVersion.playbook_id",
        cascade="all, delete-orphan",
    )
    current_version: Mapped["PlaybookVersion | None"] = relationship(
        foreign_keys=[current_version_id]
    )

    def __repr__(self) -> str:
        return f"<Playbook id={self.id} playbook_number={self.playbook_number!r} category={self.category!r}>"


class PlaybookVersion(Base, UUIDPKMixin, TimestampMixin):
    """A specific version of a playbook's content."""

    __tablename__ = "playbook_versions"
    __table_args__ = (
        UniqueConstraint("playbook_id", "version_number", name="uq_playbook_versions_playbook_id_version_number"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False, index=True
    )

    version_number: Mapped[int] = mapped_column(Integer, nullable=False)

    rollback_steps: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    validation_steps: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    variables: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft", index=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    playbook: Mapped["Playbook"] = relationship(
        back_populates="versions", foreign_keys=[playbook_id]
    )
    steps: Mapped[list["PlaybookStep"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="PlaybookStep.order"
    )

    def __repr__(self) -> str:
        return f"<PlaybookVersion id={self.id} playbook_id={self.playbook_id} version_number={self.version_number} status={self.status!r}>"


class PlaybookStep(Base, UUIDPKMixin, TimestampMixin):
    """An ordered execution step within a playbook version."""

    __tablename__ = "playbook_steps"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    playbook_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("playbook_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    order: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)

    executor: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    required_variables: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list, server_default="{}"
    )
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    continue_on_failure: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    version: Mapped["PlaybookVersion"] = relationship(back_populates="steps")

    def __repr__(self) -> str:
        return f"<PlaybookStep id={self.id} order={self.order} title={self.title!r} executor={self.executor!r}>"