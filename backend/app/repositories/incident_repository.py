"""
Repository Pattern: all direct SQLAlchemy access for Incident lives here.
The service layer (CorrelationEngine, AlertService) talks to this class
only, same convention as AlertRepository / MonitoringSourceRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.incident import Incident


class IncidentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **fields) -> Incident:
        incident = Incident(**fields)
        self.db.add(incident)
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def get(self, incident_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident | None:
        stmt = select(Incident).where(
            Incident.id == incident_id,
            Incident.tenant_id == tenant_id,
            Incident.is_deleted.is_(False),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_for_device(self, device_id: uuid.UUID) -> Incident | None:
        """The active (non-resolved) incident for a device, if one exists -
        used by CorrelationEngine to decide whether a new alert on this
        device should join an existing incident rather than start a new
        one. Not partial-unique-indexed at the DB level (unlike Alert's
        per-sensor/per-signal dedup) since "at most one open incident per
        device" is a correlation heuristic, not a hard identity invariant -
        two genuinely independent incidents on the same device are possible
        (e.g. a power issue and an unrelated software bug at different
        times), so this is a best-effort "most recent open one" lookup, not
        an enforced constraint."""
        stmt = (
            select(Incident)
            .where(
                Incident.device_id == device_id,
                Incident.status != "resolved",
                Incident.is_deleted.is_(False),
            )
            .order_by(Incident.last_alert_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_for_site(self, site_id: uuid.UUID) -> Incident | None:
        """Site-scoped counterpart to get_open_for_device() - used when an
        alert has no device_id but does have a site_id (e.g. a vendor-
        native alert not yet linked to a Device)."""
        stmt = (
            select(Incident)
            .where(
                Incident.site_id == site_id,
                Incident.status != "resolved",
                Incident.is_deleted.is_(False),
            )
            .order_by(Incident.last_alert_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_for_device_and_sensor(
        self, device_id: uuid.UUID, sensor_id: uuid.UUID
    ) -> Incident | None:
        """The most recent open incident on this device that already has a
        member alert from the SAME sensor. SSL-check dedup semantics (and any
        per-probe metric): failures of independent probes on one device
        (e.g. SSL checks of different ports) are separate incidents, while
        repeated failures of the SAME probe group together."""
        stmt = (
            select(Incident)
            .join(Alert, Alert.incident_id == Incident.id)
            .where(
                Incident.device_id == device_id,
                Alert.sensor_id == sensor_id,
                Incident.status != "resolved",
                Incident.is_deleted.is_(False),
            )
            .order_by(Incident.last_alert_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_for_signature(self, signature: str) -> Incident | None:
        """The most recent open incident whose member alerts share this
        probe signature (alerts.alert_signature). SSL-check dedup: repeated
        failures of the same host:port probe group into ONE incident, while
        failures of a different port (different signature) start their own.
        """
        stmt = (
            select(Incident)
            .join(Alert, Alert.incident_id == Incident.id)
            .where(
                Alert.alert_signature == signature,
                Incident.status != "resolved",
                Incident.is_deleted.is_(False),
            )
            .order_by(Incident.last_alert_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def add_alert(self, incident: Incident, *, occurred_at: datetime) -> Incident:
        """Bumps alert_count/last_alert_at when a NEW alert joins this
        incident. Does not touch severity - callers that need severity
        recomputed call update_severity() explicitly, since not every
        add_alert() call changes the incident's max severity."""
        incident.alert_count += 1
        # total_occurrences mirrors alert_count (migration 1be605e3f990) so
        # the API can report recurrence without recomputing it.
        incident.total_occurrences = incident.alert_count
        incident.last_alert_at = occurred_at
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def touch_occurrence(self, incident: Incident, *, occurred_at: datetime) -> Incident:
        """Advance recurrence state for an ALREADY-MEMBER alert re-triggering
        the same unchanged condition. Unlike add_alert(), this does NOT bump
        alert_count (the distinct-member count - the same alert row is not a
        new member), it only increments the recurrence tally and refreshes
        last_alert_at so the incident stays inside CorrelationEngine's
        time window. Used by the CorrelationEngine's idempotent recurrence
        short-circuit: preserving first_seen/member set while still
        reflecting latest activity."""
        incident.total_occurrences += 1
        incident.last_alert_at = occurred_at
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def update_severity(self, incident: Incident, severity: str) -> Incident:
        incident.severity = severity
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def set_root_cause(self, incident: Incident, root_cause_alert_id: uuid.UUID | None) -> Incident:
        incident.root_cause_alert_id = root_cause_alert_id
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def _transition(self, incident: Incident, status: str, ts_field: str) -> Incident:
        """Generic status transition — sets status + the named timestamp."""
        incident.status = status
        setattr(incident, ts_field, datetime.now(timezone.utc))
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def acknowledge(self, incident: Incident, acknowledged_by: uuid.UUID | None = None) -> Incident:
        incident.status = "acknowledged"
        incident.acknowledged_at = datetime.now(timezone.utc)
        if acknowledged_by is not None:
            incident.acknowledged_by = acknowledged_by
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def investigate(self, incident: Incident) -> Incident:
        return await self._transition(incident, "investigating", "investigating_at")

    async def monitor(self, incident: Incident) -> Incident:
        return await self._transition(incident, "monitoring", "monitoring_at")

    async def recover(self, incident: Incident) -> Incident:
        return await self._transition(incident, "recovered", "recovered_at")

    async def close(self, incident: Incident) -> Incident:
        return await self._transition(incident, "closed", "closed_at")

    async def archive(self, incident: Incident) -> Incident:
        return await self._transition(incident, "archived", "archived_at")

    async def resolve(self, incident: Incident) -> Incident:
        incident.status = "resolved"
        incident.resolved_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def set_priority_scores(
        self,
        incident: Incident,
        *,
        priority: str,
        operational_risk_score: float,
        business_risk_score: float,
        technical_risk_score: float,
        ai_risk_score: float,
        incident_confidence_score: float,
        business_impact_score: float,
        escalation_level: str,
        recommended_team: str,
        recommended_response_time_minutes: int,
        recommended_next_action: str,
        recommended_sop: str,
        priority_reasoning: dict,
        calculated_at: datetime,
    ) -> Incident:
        """AI Priority Engine sprint: persists one full scoring pass.

        Respects `manual_priority_override` - if set, `priority` itself is
        left untouched (the operator's pinned value wins) but every risk/
        confidence score and the reasoning breakdown are still updated, so
        the dashboard always shows current, real scores even when the
        top-level priority is manually pinned.
        """
        if incident.manual_priority_override is None:
            incident.priority = priority
        incident.operational_risk_score = operational_risk_score
        incident.business_risk_score = business_risk_score
        incident.technical_risk_score = technical_risk_score
        incident.ai_risk_score = ai_risk_score
        incident.incident_confidence_score = incident_confidence_score
        incident.business_impact_score = business_impact_score
        incident.escalation_level = escalation_level
        incident.recommended_team = recommended_team
        incident.recommended_response_time_minutes = recommended_response_time_minutes
        incident.recommended_next_action = recommended_next_action
        incident.recommended_sop = recommended_sop
        incident.priority_reasoning = priority_reasoning
        incident.priority_calculated_at = calculated_at
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def set_manual_override(
        self,
        incident: Incident,
        *,
        priority: str | None,
        overridden_by: uuid.UUID | None,
    ) -> Incident:
        """Sets or clears the manual priority pin. Passing `priority=None`
        clears the override (PriorityEngine resumes controlling `priority`
        on the next recompute); passing a value pins it AND immediately
        applies it to `priority` itself so the change is visible before
        the next scheduled recompute."""
        incident.manual_priority_override = priority
        incident.priority_overridden_by = overridden_by
        incident.priority_overridden_at = datetime.now(timezone.utc) if priority is not None else None
        if priority is not None:
            incident.priority = priority
        await self.db.flush()
        await self.db.refresh(incident)
        return incident

    async def list_prioritized(
        self,
        tenant_id: uuid.UUID,
        *,
        status: str | None = "open",
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Incident]:
        """Incidents ordered by priority (P1 first) then most recent
        activity - the "highest priority incidents" dashboard widget and
        GET /api/incidents/prioritized. Incidents with no priority yet
        (never scored) sort last, not first, so an unscored incident never
        outranks a genuinely scored P1."""
        priority_rank = case(
            (Incident.priority == "P1", 0),
            (Incident.priority == "P2", 1),
            (Incident.priority == "P3", 2),
            (Incident.priority == "P4", 3),
            else_=4,
        )
        stmt = select(Incident).where(Incident.tenant_id == tenant_id, Incident.is_deleted.is_(False))
        if status is not None:
            stmt = stmt.where(Incident.status == status)
        if priority is not None:
            stmt = stmt.where(Incident.priority == priority)
        stmt = stmt.order_by(priority_rank, Incident.last_alert_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def priority_distribution(self, tenant_id: uuid.UUID, *, status: str | None = "open") -> dict[str, int]:
        """Counts of open incidents per priority level - the Priority
        Distribution dashboard widget."""
        stmt = (
            select(Incident.priority, func.count())
            .where(Incident.tenant_id == tenant_id, Incident.is_deleted.is_(False))
            .group_by(Incident.priority)
        )
        if status is not None:
            stmt = stmt.where(Incident.status == status)
        result = await self.db.execute(stmt)
        distribution = {"P1": 0, "P2": 0, "P3": 0, "P4": 0, "unscored": 0}
        for priority, count in result.all():
            key = priority or "unscored"
            distribution[key] = distribution.get(key, 0) + count
        return distribution

    async def intelligence_counts(self, tenant_id: uuid.UUID) -> tuple[int, int]:
        """Single-scan aggregates backing ``GET /api/incidents/intelligence``.

        Replaces the previous three sequential ``COUNT(*)`` round trips (open +
        acknowledged + investigating) plus a capped 1000-row ORM fetch with ONE
        conditional-aggregate scan of the tenant's incidents. Measured on the
        real 2.4M-incident tenant: 9.9s -> ~2.4s.

        Definitions are unchanged:
          * ``active``     = open + acknowledged + investigating
          * ``correlated`` = open incidents with more than one member alert
        The previous implementation derived ``correlated`` from only the newest
        1000 open incidents, so it was truncated for large tenants; it is now
        exact. The field name and its meaning are unchanged.
        """
        stmt = (
            select(
                func.count()
                .filter(Incident.status.in_(("open", "acknowledged", "investigating")))
                .label("active"),
                func.count()
                .filter((Incident.status == "open") & (Incident.alert_count > 1))
                .label("correlated"),
            )
            .select_from(Incident)
            .where(Incident.tenant_id == tenant_id, Incident.is_deleted.is_(False))
        )
        row = (await self.db.execute(stmt)).one()
        return int(row[0]), int(row[1])

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Incident]:
        stmt = select(Incident).where(Incident.tenant_id == tenant_id, Incident.is_deleted.is_(False))
        if site_id is not None:
            stmt = stmt.where(Incident.site_id == site_id)
        if device_id is not None:
            stmt = stmt.where(Incident.device_id == device_id)
        if severity is not None:
            stmt = stmt.where(Incident.severity == severity)
        if status is not None:
            stmt = stmt.where(Incident.status == status)
        stmt = stmt.order_by(Incident.last_alert_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
    ) -> int:
        stmt = select(func.count()).select_from(Incident).where(
            Incident.tenant_id == tenant_id, Incident.is_deleted.is_(False)
        )
        if site_id is not None:
            stmt = stmt.where(Incident.site_id == site_id)
        if device_id is not None:
            stmt = stmt.where(Incident.device_id == device_id)
        if severity is not None:
            stmt = stmt.where(Incident.severity == severity)
        if status is not None:
            stmt = stmt.where(Incident.status == status)
        result = await self.db.execute(stmt)
        return result.scalar_one()
