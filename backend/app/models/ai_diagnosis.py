"""
AI Diagnosis model.

Structured AI diagnostic output for NOC events.
AI does NOT operate on random database dumps — it receives structured context
and produces structured output with evidence, confidence, and recommendations.

Lifecycle: pending -> analyzing -> diagnosed -> validated -> actioned -> verified
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.noc_event import NocEvent
    from app.models.tenant import Tenant


class AIDiagnosis(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    """Structured AI diagnostic result for a NOC event."""

    __tablename__ = "ai_diagnoses"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # -- Linkage --
    noc_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("noc_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The analyst who requested this diagnosis (optional)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )

    # -- Status --
    # pending | analyzing | diagnosed | insufficient_evidence | validated | actioned | verified | dismissed
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending", index=True
    )

    # -- Diagnosis output --
    diagnosis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_candidates: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Each candidate: {"cause": "...", "confidence": 85.0, "evidence": [...]}

    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    # Structured evidence: [{"source": "...", "finding": "...", "confidence": 90}]

    impact_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_actions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Each action: {"action": "...", "risk": "low", "automation_possible": true}

    risk_assessment: Mapped[str | None] = mapped_column(String(20), nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    verification_plan: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Confidence & quality --
    overall_confidence: Mapped[float] = mapped_column(Integer, nullable=False, default=0)
    evidence_sufficiency: Mapped[str] = mapped_column(
        String(20), nullable=False, default="insufficient"
    )
    # sufficient | partial | insufficient

    # -- Context snapshot (for auditability) --
    # What context was provided to AI (tenant, site, topology, events, metrics)
    context_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # -- Analyst feedback --
    analyst_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    analyst_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyst_revised_diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- Timestamps --
    analysis_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- Model metadata --
    model_used: Mapped[str] = mapped_column(String(100), nullable=False, default="ollama-local")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # -- Relationships --
    tenant: Mapped["Tenant"] = relationship()
    noc_event: Mapped["NocEvent"] = relationship()

    def __repr__(self) -> str:
        return (
            f"<AIDiagnosis id={self.id} noc_event_id={self.noc_event_id} "
            f"status={self.status!r} confidence={self.overall_confidence}>"
        )
