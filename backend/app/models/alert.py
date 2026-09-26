"""
Alert model. Represents an actionable event derived from a Sensor status
change (or, in future, a manual/webhook trigger). Tenant-scoped; carries
denormalized site_id/device_id (same pattern Device uses for tenant_id) so
list/filter queries never need an extra join just to show "which site".

Lifecycle: open -> acknowledged -> resolved. A sensor that keeps reporting
the same bad status on repeated syncs bumps occurrence_count on the existing
alert instead of creating a new row each time - this is what prevents alert
storms from a flapping sensor. The partial unique index below enforces "at
most one non-resolved alert per sensor" at the database level, so that
invariant holds even under concurrent syncs, not just in application code.

Alert Normalization & Correlation Engine sprint (additive columns below the
original set): every Alert now carries the full "Standard Alert Model" the
sprint's brief asked for - interface_id, a normalized `category` (distinct
from the pre-existing `metric_type`, which stays a sensor-derived free-form
label), a denormalized `vendor`/`region` pair (resolved once at creation
time from Device/Site, same denormalization pattern already used for
site_id/device_id), and the correlation/incident fields
(`correlation_id`, `incident_id`, `parent_alert_id`,
`is_root_cause_candidate`, `is_suppressed`) that let `CorrelationEngine`
group related alerts into an `Incident` without introducing a separate
join table. See `app/services/alerting/correlation_engine.py` for how
these fields get populated.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.device import Device
    from app.models.incident import Incident
    from app.models.interface import Interface
    from app.models.monitoring_source import MonitoringSource
    from app.models.sensor import Sensor
    from app.models.site import Site
    from app.models.tenant import Tenant


class Alert(Base, UUIDPKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_active_per_sensor",
            "sensor_id",
            unique=True,
            postgresql_where=text(
                "status <> 'resolved' AND sensor_id IS NOT NULL AND is_deleted = false"
            ),
        ),
        Index(
            "uq_alerts_active_per_signal",
            "monitoring_source_id",
            "signal_external_id",
            unique=True,
            postgresql_where=text(
                "status <> 'resolved' AND monitoring_source_id IS NOT NULL "
                "AND signal_external_id IS NOT NULL AND is_deleted = false"
            ),
        ),
        # Bounded AI-insight / priority query paths (added by migration
        # b8e3f7a9c1d4); declared here so the model is the honest source of
        # truth instead of `alembic check` proposing to drop live indexes.
        Index(
            "ix_alerts_tenant_status_occurrence_last",
            "tenant_id", "status", "occurrence_count", "last_occurred_at",
        ),
        Index(
            "ix_alerts_tenant_status_severity_last",
            "tenant_id", "status", "severity", "last_occurred_at",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sensor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sensors.id", ondelete="SET NULL"), nullable=True, index=True
    )
    monitoring_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("monitoring_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Vendor-native alert/alarm ID from the monitoring source itself, distinct
    # from sensor_id (which identifies our own threshold-derived alerts). Used
    # together with monitoring_source_id to dedupe vendor-native alert signals.
    signal_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # critical | warning | info
    severity: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # open | acknowledged | resolved
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open", index=True
    )

    # Denormalized from the triggering sensor so list/filter queries never
    # need a join just to filter by metric type.
    metric_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # Where this alert originated: monitoring_sync | manual | webhook (future).
    source: Mapped[str] = mapped_column(
        String(30), nullable=False, default="monitoring_sync", server_default="monitoring_sync", index=True
    )

    triggered_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    auto_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # -- Standard Alert Model additions (Correlation Engine sprint) --------
    # Best-effort link to the Interface this alert concerns (e.g. an
    # interface_traffic sensor's alert). Nullable - most alerts (device-
    # level, vendor-native) have no interface context.
    interface_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("interfaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Normalized alert category (availability | performance | capacity |
    # configuration | security | connectivity | other), distinct from the
    # pre-existing `metric_type` (a raw sensor-derived label like "cpu" or
    # "ping"). Populated by EventClassifier - free-form string, not an enum,
    # so new classification rules never require a migration. Nullable only
    # for legacy rows created before this column existed.
    category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    # Denormalized from Device.vendor (or the MonitoringSource's vendor for
    # vendor-native alerts with no device link) at alert-creation time - same
    # denormalization rationale as device_id/site_id above.
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # Denormalized from Site.region at alert-creation time.
    region: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # Groups alerts the CorrelationEngine considers "the same underlying
    # condition" (e.g. repeated PRTG messages for one flapping link) even
    # across the sensor/signal identity boundaries the existing dedup
    # indexes use. Distinct from `incident_id`: a correlation_id groups
    # near-duplicate alerts, an incident_id groups causally-related alerts
    # (e.g. a switch outage plus every downstream device alert it caused).
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Parent-child alert relationship within an incident (e.g. a device-down
    # alert as parent, its dependent interface/sensor alerts as children).
    # Self-referential FK; ondelete="SET NULL" so deleting a parent alert
    # never cascades into deleting its children.
    parent_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # True when CorrelationEngine's root-cause heuristic identifies this
    # alert as the most likely originating cause within its incident (see
    # correlation_engine.py's root-cause heuristic for the exact rule).
    is_root_cause_candidate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # True when this alert is a suppressed child of a detected root cause -
    # excluded from the default Active Alerts view (still counted in
    # statistics) rather than deleted, so the underlying signal is never
    # lost. See correlation_engine.py's suppression rule.
    is_suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )

    # Probe dedup signature (migration 1be605e3f990; additive) - e.g.
    # "ssl_security:SSL Security Check - host:993". CorrelationEngine uses
    # it so repeated failures of the SAME probe group into one incident,
    # while a different probe (different port) starts its own.
    alert_signature: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # -- AI Priority Engine addition -----------------------------------
    # 0-100: how confident the classification/normalization pipeline is
    # that this alert's category/severity are correct (e.g. a vendor-
    # native signal with a matched device and a keyword-matched category
    # scores higher than one with no device link and a fallback "other"
    # category). Set once at alert-creation time by AlertService, read by
    # PriorityEngine/CorrelationEngine's confidence rollups. Nullable for
    # legacy rows predating this column.
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    tenant: Mapped["Tenant"] = relationship()
    site: Mapped["Site | None"] = relationship()
    device: Mapped["Device | None"] = relationship()
    sensor: Mapped["Sensor | None"] = relationship()
    interface: Mapped["Interface | None"] = relationship()
    monitoring_source: Mapped["MonitoringSource | None"] = relationship()
    incident: Mapped["Incident | None"] = relationship(back_populates="alerts", foreign_keys=[incident_id])
    parent_alert: Mapped["Alert | None"] = relationship(remote_side="Alert.id")

    def __repr__(self) -> str:
        return f"<Alert id={self.id} severity={self.severity!r} status={self.status!r}>"