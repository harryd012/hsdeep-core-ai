"""
SOPRecommendationDecision model.

An operator's answer to one SOP recommended for one incident: `accepted`
(this is the procedure we are following) or `dismissed` (not relevant here,
with an optional reason). One row per (incident, SOP); changing the answer
updates the row and is audited.

The row keeps what the operator saw when deciding: the SOP version and the
confidence score at that moment, so later feedback analysis is not skewed by
re-scored recommendations.

Accepting runs nothing. Nothing links an SOP to a runbook yet, so an accepted
SOP is a recorded choice, not an execution.

`decided_by` is the signed-in user, or NULL for an API key (which has no user
identity), with no foreign key: users are soft-deleted and the row stays.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin

SOP_DECISIONS = ("accepted", "dismissed")


class SOPRecommendationDecision(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "sop_recommendation_decisions"
    __table_args__ = (
        UniqueConstraint("incident_id", "sop_id", name="uq_sop_recommendation_decisions_incident_sop"),
        CheckConstraint("decision IN ('accepted', 'dismissed')", name="ck_sop_recommendation_decisions_decision"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sops.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sop_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sop_versions.id", ondelete="SET NULL"), nullable=True
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
