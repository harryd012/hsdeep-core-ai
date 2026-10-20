"""
Incident model. A causally-related group of Alerts that the Correlation
Engine has grouped together - e.g. a switch outage plus every downstream
device/interface alert it triggered. Distinct from an Alert's
`correlation_id` (which groups near-duplicate signals of the SAME
condition): an Incident groups DIFFERENT alerts the engine believes share
one root cause.

Lifecycle mirrors Alert's (open -> acknowledged -> resolved) but is
independently tracked - an Incident can still be "open" after its root-
cause Alert auto-resolves, if child alerts are still active, and vice
versa. `AlertService`/`CorrelationEngine` are responsible for keeping this
in sync; this model has no triggers of its own.

AI Priority Engine sprint (additive columns below the original set): every
Incident now carries a deterministic priority (P1-P4), four risk scores,
two confidence scores, and a set of AI-generated recommendations - all
computed by `app/services/priority/` and persisted here so the API/
dashboard never has to recompute them on every read. `priority_reasoning`
stores the human-readable "why" for every score, satisfying the sprint's
explainability requirement. `manual_priority_override`/`overridden_by`
let an operator pin a priority the engine will never silently recompute
over - see `PriorityService.set_manual_override()`.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.device import Device
    from app.models.site import Site
    from app.models.tenant import Tenant
    from app.models.user import User


class Incident(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "incidents"
    __table_args__ = (
        # Command Center / incident-list read paths. Declared here so the model
        # stays the honest source of truth instead of `alembic check` proposing
        # to drop live indexes.
        #
        # Every predicate below is written `is_deleted IS false`, NOT
        # `is_deleted = false`, and that spelling is load-bearing: repositories
        # filter with `Incident.is_deleted.is_(False)`, which SQLAlchemy renders
        # as `is_deleted IS false`. PostgreSQL cannot prove that `IS false`
        # implies a partial-index predicate spelled `= false`, so an index
        # written the other way is silently NEVER used and the query falls back
        # to a full scan (measured 11,456ms vs 0.9ms on `list()`). `is_deleted`
        # is NOT NULL, so the two spellings are logically identical - only the
        # planner's predicate proof differs. See migration a4c7e2f91b36.
        #
        # `tenant_id` LEADS every index, so tenant isolation stays enforced in
        # SQL as an index-range bound rather than being filtered in Python.
        Index(
            "ix_incidents_tenant_last_alert_active",
            "tenant_id",
            text("last_alert_at DESC"),
            postgresql_where=text("is_deleted IS false"),
        ),
        Index(
            "ix_incidents_tenant_opened_active",
            "tenant_id",
            "opened_at",
            postgresql_where=text("is_deleted IS false"),
        ),
        Index(
            "ix_incidents_tenant_resolved_active",
            "tenant_id",
            "resolved_at",
            postgresql_where=text("is_deleted IS false"),
        ),
        Index(
            "ix_incidents_tenant_status_severity_active",
            "tenant_id",
            "status",
            "severity",
            postgresql_where=text("is_deleted IS false"),
        ),
        Index(
            "ix_incidents_tenant_open_correlated",
            "tenant_id",
            postgresql_where=text("status = 'open' AND alert_count > 1 AND is_deleted IS false"),
        ),
        Index(
            "uq_incidents_open_signature",
            "tenant_id",
            "incident_signature",
            unique=True,
            postgresql_where=text("status = 'open' AND incident_signature IS NOT NULL AND is_deleted IS false"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalized "where"/"what" for the incident as a whole - taken from
    # the root-cause alert (or the first alert grouped in, if no root cause
    # has been identified yet) at creation time, same denormalization
    # rationale as Alert.site_id/device_id.
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # critical | warning | info - the highest severity among member alerts
    # at last recompute, per CorrelationEngine.recompute_incident_severity().
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # open | acknowledged | resolved | ... — String(30) to match the DB column
    # (widened by revision abc1d2e3f4a5). The model previously declared
    # String(20) while the live column was VARCHAR(30), which `alembic check`
    # reported as a modify_type discrepancy.
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="open", server_default="open", index=True
    )

    # The Alert.id CorrelationEngine identified as the most likely root
    # cause, if any. Nullable - not every incident has a confident root
    # cause candidate. Deliberately NOT a FK-with-cascade: if that alert row
    # is ever hard-deleted, the incident should keep existing with this
    # field going stale/null rather than being deleted itself.
    root_cause_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )

    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    #: Normalized correlation signature (see app.services.alerting.signature).
    #: Nullable + no backfill: historical rows predate this column and are
    #: deduplicated by the separate cleanup script, not by a backfill here.
    incident_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Why the incident was auto-closed by the stale-close job (None unless
    #: closed automatically), e.g. "no new alert for 24h".
    closure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_alert_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- AI Priority Engine additions ---------------------------------------
    # P1 (critical, immediate response) | P2 (high, respond within SLA) |
    # P3 (medium, normal queue) | P4 (low, background processing).
    # Nullable - an incident that hasn't been scored yet (or predates this
    # column) has no priority rather than a fabricated default.
    priority: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)

    # 0-100 scores, each independently explainable via priority_reasoning.
    operational_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    business_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    technical_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 0-100: how confident PriorityEngine is that `priority` reflects
    # reality, given the completeness of the inputs it had (e.g. no
    # BusinessService mapped to the affected device lowers confidence
    # without lowering the priority itself).
    incident_confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    business_impact_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Deterministic AI Decision Engine outputs - free-form strings (not
    # enums/FKs) since no Team/SOP catalog model exists yet; see
    # app/services/priority/decision_engine.py for the exact rule set that
    # produces each value.
    escalation_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    recommended_team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recommended_response_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommended_next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_sop: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Full transparent breakdown of every score above - e.g.
    # {"priority": {"value": "P1", "reasons": [...]}, "business_risk_score":
    # {"value": 82.0, "reasons": [...]}} - satisfies the sprint's
    # explainability requirement without needing a separate audit table.
    priority_reasoning: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    priority_calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Manual override: once set, PriorityEngine.recompute() must leave
    # `priority` untouched (still recomputes and stores the risk scores
    # for visibility, but never overwrites the pinned priority) until the
    # override is explicitly cleared. See PriorityService.set_manual_override().
    manual_priority_override: Mapped[str | None] = mapped_column(String(10), nullable=True)
    priority_overridden_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    priority_overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -- CMDB / lifecycle additions (migration 1be605e3f990; additive) -------
    business_service_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("business_services.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Lifecycle timestamps (open -> investigating -> monitoring -> recovered
    # -> closed -> archived); each set once when the incident enters that stage.
    investigating_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    monitoring_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_occurrences: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    correlation_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    root_cause_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_evidence: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    automation_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    affected_assets: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_after_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1440, server_default="1440"
    )

    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site | None"] = relationship()
    device: Mapped["Device | None"] = relationship()
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="incident", foreign_keys="Alert.incident_id"
    )

    def __repr__(self) -> str:
        return f"<Incident id={self.id} severity={self.severity!r} status={self.status!r} alerts={self.alert_count}>"
