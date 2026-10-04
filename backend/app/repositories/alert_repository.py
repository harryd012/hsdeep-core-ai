"""
Repository Pattern: all direct SQLAlchemy access for Alert lives here. The
service layer talks to this class only, same convention as
MonitoringSourceRepository / SensorRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.models.sensor import Sensor
from app.models.site import Site

_ALL_SEVERITIES = ("critical", "warning", "info")
_ALL_STATUSES = ("open", "acknowledged", "resolved")


class AlertRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_for_sensor(self, sensor_id: uuid.UUID) -> Alert | None:
        """An 'active' alert is open or acknowledged - i.e. not yet resolved.
        Used to decide whether a re-triggering sensor should bump an
        existing incident's occurrence_count or start a fresh one."""
        stmt = select(Alert).where(
            Alert.sensor_id == sensor_id,
            Alert.status != "resolved",
            Alert.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_for_signal(
        self, monitoring_source_id: uuid.UUID, signal_external_id: str
    ) -> Alert | None:
        """An 'active' alert is open or acknowledged - i.e. not yet resolved.
        Vendor-native counterpart to get_active_for_sensor(), keyed on the
        vendor's own alert/alarm identity instead of a Sensor row."""
        stmt = select(Alert).where(
            Alert.monitoring_source_id == monitoring_source_id,
            Alert.signal_external_id == signal_external_id,
            Alert.status != "resolved",
            Alert.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active_for_insights(
        self,
        tenant_id: uuid.UUID,
        *,
        severity: str | None = None,
        min_occurrence_count: int | None = None,
        triggered_before: datetime | None = None,
        limit: int = 200,
    ) -> list[Alert]:
        """A BOUNDED read of active (non-resolved) alerts for the AI Insight
        rules. Unlike the general `list()` (which can be called with very
        large limits and returns full join-enriched rows), this only returns
        plain Alert ORM rows ordered by most-recent activity, capped at a small
        `limit`, and filters server-side. The Insight service never loads a
        tenant's entire alerts table into Python - on a high-volume tenant
        with hundreds of thousands of resolved/historical rows this would
        otherwise be the dominant cost behind Copilot/AI timeouts.

        Filters are optional and combine with AND:
          * severity           - only this severity (e.g. 'critical')
          * min_occurrence_count - re-triggered at least this many times
          * triggered_before   - triggered at or before this instant (e.g. for
                                 the sustained-critical "open > 30 min" rule)
        """
        stmt = select(Alert).where(
            Alert.tenant_id == tenant_id,
            Alert.is_deleted.is_(False),
            Alert.status != "resolved",
        )
        if severity is not None:
            stmt = stmt.where(Alert.severity == severity)
        if min_occurrence_count is not None:
            stmt = stmt.where(Alert.occurrence_count >= min_occurrence_count)
        if triggered_before is not None:
            stmt = stmt.where(Alert.triggered_at <= triggered_before)
        stmt = stmt.order_by(Alert.last_occurred_at.desc()).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **fields) -> Alert:
        alert = Alert(**fields)
        self.db.add(alert)
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def record_occurrence(
        self,
        alert: Alert,
        *,
        triggered_value: float | None,
        threshold_value: float | None,
        severity: str,
        occurred_at: datetime,
    ) -> Alert:
        alert.occurrence_count += 1
        alert.last_occurred_at = occurred_at
        alert.triggered_value = triggered_value
        alert.threshold_value = threshold_value
        if severity != alert.severity:
            # Condition changed magnitude between syncs (e.g. warning -> critical)
            # without ever recovering - reflect the worse/updated severity.
            alert.severity = severity
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def acknowledge(self, alert: Alert, *, acknowledged_by: uuid.UUID) -> Alert:
        alert.status = "acknowledged"
        alert.acknowledged_at = datetime.now(timezone.utc)
        alert.acknowledged_by = acknowledged_by
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def resolve(self, alert: Alert, *, resolved_by: uuid.UUID | None, auto: bool) -> Alert:
        alert.status = "resolved"
        alert.resolved_at = datetime.now(timezone.utc)
        alert.resolved_by = resolved_by
        alert.auto_resolved = auto
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def set_enrichment_fields(
        self,
        alert: Alert,
        *,
        category: str | None = None,
        vendor: str | None = None,
        region: str | None = None,
        interface_id: uuid.UUID | None = None,
    ) -> Alert:
        """Additive setter for the Standard Alert Model fields
        (Correlation Engine sprint) - only overwrites a field when the
        caller passes a non-None value, so a partial enrichment call never
        clobbers a previously-resolved field with None."""
        if category is not None:
            alert.category = category
        if vendor is not None:
            alert.vendor = vendor
        if region is not None:
            alert.region = region
        if interface_id is not None:
            alert.interface_id = interface_id
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def set_confidence_score(self, alert: Alert, *, confidence_score: float) -> Alert:
        """AI Priority Engine sprint: persists AlertConfidenceScorer's
        output. Additive setter, same pattern as set_enrichment_fields()."""
        alert.confidence_score = confidence_score
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def set_alert_signature(self, alert: Alert, signature: str) -> None:
        """Persist the probe dedup signature on an alert (additive column
        from migration 1be605e3f990)."""
        alert.alert_signature = signature
        await self.db.flush()

    async def set_correlation_fields(
        self,
        alert: Alert,
        *,
        correlation_id: uuid.UUID | None,
        incident_id: uuid.UUID | None,
        parent_alert_id: uuid.UUID | None = None,
        is_root_cause_candidate: bool | None = None,
        is_suppressed: bool | None = None,
    ) -> Alert:
        """Setter for CorrelationEngine's grouping decisions.

        `correlation_id`/`incident_id` are ALWAYS assigned exactly as
        passed (including None, to support clearing them e.g. if an
        incident is later deleted) - callers must pass the value they want,
        there is no "omit to leave unchanged" behavior for these two.
        `parent_alert_id`/`is_root_cause_candidate`/`is_suppressed` default
        to None meaning "leave unchanged", matching the additive-update
        convention used by `set_enrichment_fields()` above, since those
        three are typically set independently of a correlation_id/
        incident_id assignment (e.g. marking root-cause after the alert
        already joined an incident in an earlier call).
        """
        alert.correlation_id = correlation_id
        alert.incident_id = incident_id
        if parent_alert_id is not None:
            alert.parent_alert_id = parent_alert_id
        if is_root_cause_candidate is not None:
            alert.is_root_cause_candidate = is_root_cause_candidate
        if is_suppressed is not None:
            alert.is_suppressed = is_suppressed
        await self.db.flush()
        await self.db.refresh(alert)
        return alert

    async def get(self, alert_id: uuid.UUID, tenant_id: uuid.UUID) -> Alert | None:
        stmt = select(Alert).where(
            Alert.id == alert_id, Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        sensor_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        metric_type: str | None = None,
        source: str | None = None,
        vendor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        incident_id: uuid.UUID | None = None,
        category: str | None = None,
        is_suppressed: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Alert]:
        stmt = (
            select(Alert, Device.name, Site.name, Sensor.name)
            .outerjoin(Device, Device.id == Alert.device_id)
            .outerjoin(Site, Site.id == Alert.site_id)
            .outerjoin(Sensor, Sensor.id == Alert.sensor_id)
            .where(Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False))
        )
        if site_id is not None:
            stmt = stmt.where(Alert.site_id == site_id)
        if device_id is not None:
            stmt = stmt.where(Alert.device_id == device_id)
        if sensor_id is not None:
            stmt = stmt.where(Alert.sensor_id == sensor_id)
        if severity is not None:
            stmt = stmt.where(Alert.severity == severity)
        if status is not None:
            stmt = stmt.where(Alert.status == status)
        if metric_type is not None:
            stmt = stmt.where(Alert.metric_type == metric_type)
        if source is not None:
            stmt = stmt.where(Alert.source == source)
        if vendor is not None:
            stmt = stmt.where(Device.vendor == vendor)
        if since is not None:
            stmt = stmt.where(Alert.triggered_at >= since)
        if until is not None:
            stmt = stmt.where(Alert.triggered_at <= until)
        if incident_id is not None:
            stmt = stmt.where(Alert.incident_id == incident_id)
        if category is not None:
            stmt = stmt.where(Alert.category == category)
        if is_suppressed is not None:
            stmt = stmt.where(Alert.is_suppressed == is_suppressed)
        stmt = stmt.order_by(Alert.last_occurred_at.desc()).limit(limit).offset(offset)

        result = await self.db.execute(stmt)
        alerts: list[Alert] = []
        for alert, device_name, site_name, sensor_name in result.all():
            # Plain instance attributes (not mapped columns) - read by
            # AlertOut.model_validate(..., from_attributes=True).
            alert.device_name = device_name
            alert.site_name = site_name
            alert.sensor_name = sensor_name
            alerts.append(alert)
        return alerts

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        sensor_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        metric_type: str | None = None,
        source: str | None = None,
        vendor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        incident_id: uuid.UUID | None = None,
        category: str | None = None,
        is_suppressed: bool | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Alert).where(
            Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False)
        )
        if vendor is not None:
            stmt = stmt.outerjoin(Device, Device.id == Alert.device_id).where(Device.vendor == vendor)
        if site_id is not None:
            stmt = stmt.where(Alert.site_id == site_id)
        if device_id is not None:
            stmt = stmt.where(Alert.device_id == device_id)
        if sensor_id is not None:
            stmt = stmt.where(Alert.sensor_id == sensor_id)
        if severity is not None:
            stmt = stmt.where(Alert.severity == severity)
        if status is not None:
            stmt = stmt.where(Alert.status == status)
        if metric_type is not None:
            stmt = stmt.where(Alert.metric_type == metric_type)
        if source is not None:
            stmt = stmt.where(Alert.source == source)
        if since is not None:
            stmt = stmt.where(Alert.triggered_at >= since)
        if until is not None:
            stmt = stmt.where(Alert.triggered_at <= until)
        if incident_id is not None:
            stmt = stmt.where(Alert.incident_id == incident_id)
        if category is not None:
            stmt = stmt.where(Alert.category == category)
        if is_suppressed is not None:
            stmt = stmt.where(Alert.is_suppressed == is_suppressed)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def occurrence_volume(self, tenant_id: uuid.UUID) -> tuple[int, int, int]:
        """Raw vs displayed vs suppressed alert volume from ONE scan.

        The authoritative inputs for the ``duplicate_alerts_removed`` metric on
        ``GET /api/incidents/intelligence``, using the definitions documented on
        ``AlertIntelligenceService.get_metrics()`` (the Sprint 15 service that
        the endpoint's single-scan aggregates superseded — see
        docs/architecture/phase-3-1-backend-contract-integrity.md):

          * ``raw``        - ``SUM(occurrence_count)`` over every non-deleted
                             alert: how many individual sensor readings would
                             have been shown without dedup/suppression.
          * ``displayed``  - rows an operator actually sees (non-deleted, not
                             suppressed).
          * ``suppressed`` - non-deleted suppressed rows.

        Returning all three from one scan keeps this to a single pass over the
        tenant's alerts, and it is what lets ``duplicate_alerts_removed`` be its
        own measurement rather than a copy of ``suppressed_alerts`` (which is
        what it was before PHASE 3.1). Filters mirror ``count()``'s base clause
        exactly (``tenant_id`` + not soft-deleted), so ``suppressed`` here is
        identical to ``count(tenant_id, is_suppressed=True)``.
        """
        stmt = (
            select(
                func.coalesce(func.sum(Alert.occurrence_count), 0),
                func.count().filter(Alert.is_suppressed.is_(False)),
                func.count().filter(Alert.is_suppressed.is_(True)),
            )
            .select_from(Alert)
            .where(Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False))
        )
        row = (await self.db.execute(stmt)).one()
        return int(row[0]), int(row[1]), int(row[2])

    async def summary(self, tenant_id: uuid.UUID) -> dict:
        stmt = (
            select(Alert.severity, Alert.status, func.count())
            .where(Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False))
            .group_by(Alert.severity, Alert.status)
        )
        result = await self.db.execute(stmt)

        by_severity = dict.fromkeys(_ALL_SEVERITIES, 0)
        by_status = dict.fromkeys(_ALL_STATUSES, 0)
        total = 0
        for severity, status, count in result.all():
            by_severity[severity] = by_severity.get(severity, 0) + count
            by_status[status] = by_status.get(status, 0) + count
            total += count

        return {"total": total, "by_severity": by_severity, "by_status": by_status}

    async def statistics(self, tenant_id: uuid.UUID) -> dict:
        """Alert Statistics API - extends summary() with the Correlation
        Engine sprint's category/suppression/correlation breakdowns.
        Reuses the same base filter (tenant-scoped, not soft-deleted) as
        summary() rather than duplicating it, then issues 3 additional
        grouped/count queries specific to the new fields."""
        base = await self.summary(tenant_id)

        category_stmt = (
            select(Alert.category, func.count())
            .where(Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False))
            .group_by(Alert.category)
        )
        category_result = await self.db.execute(category_stmt)
        by_category: dict[str, int] = {}
        for category, count in category_result.all():
            key = category or "other"
            by_category[key] = by_category.get(key, 0) + count

        suppressed_stmt = select(func.count()).select_from(Alert).where(
            Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False), Alert.is_suppressed.is_(True)
        )
        suppressed_count = (await self.db.execute(suppressed_stmt)).scalar_one()

        correlated_stmt = select(func.count()).select_from(Alert).where(
            Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False), Alert.incident_id.isnot(None)
        )
        correlated_count = (await self.db.execute(correlated_stmt)).scalar_one()

        incident_count_stmt = select(func.count(func.distinct(Alert.incident_id))).where(
            Alert.tenant_id == tenant_id, Alert.is_deleted.is_(False), Alert.incident_id.isnot(None)
        )
        incident_count = (await self.db.execute(incident_count_stmt)).scalar_one()

        return {
            **base,
            "by_category": by_category,
            "suppressed_count": suppressed_count,
            "correlated_count": correlated_count,
            "incident_count": incident_count,
        }

    async def counts_by_device(
        self, tenant_id: uuid.UUID, *, statuses: frozenset[str] | None = None
    ) -> dict[uuid.UUID, int]:
        """Alert count per device, in ONE query.

        Added for the topology graph, whose node cards show a per-device alert
        count: calling `count(tenant_id, device_id=...)` once per node would be
        an N+1 on every page render. Mirrors the aggregation style of the
        existing `summary()`/`statistics()`.

        Alerts with no `device_id` are omitted (they cannot be attributed to a
        node), so a returned count never exceeds that node's own alerts.
        """
        stmt = select(Alert.device_id, func.count()).where(
            Alert.tenant_id == tenant_id,
            Alert.is_deleted.is_(False),
            Alert.device_id.isnot(None),
        )
        if statuses:
            stmt = stmt.where(Alert.status.in_(tuple(statuses)))
        stmt = stmt.group_by(Alert.device_id)
        rows = (await self.db.execute(stmt)).all()
        return {device_id: count for device_id, count in rows}
