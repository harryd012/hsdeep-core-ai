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

    async def get_open_for_device(self, device_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident | None:
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
                Incident.tenant_id == tenant_id,
                Incident.device_id == device_id,
                Incident.status != "resolved",
                Incident.is_deleted.is_(False),
            )
            .order_by(Incident.last_alert_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_open_for_site(self, site_id: uuid.UUID, tenant_id: uuid.UUID) -> Incident | None:
        """Site-scoped counterpart to get_open_for_device() - used when an
        alert has no device_id but does have a site_id (e.g. a vendor-
        native alert not yet linked to a Device)."""
        stmt = (
            select(Incident)
            .where(
                Incident.tenant_id == tenant_id,
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
        self, device_id: uuid.UUID, sensor_id: uuid.UUID, tenant_id: uuid.UUID
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
                Incident.tenant_id == tenant_id,
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

    async def get_open_for_signature(self, signature: str, tenant_id: uuid.UUID) -> Incident | None:
        """The most recent open incident whose stored probe signature equals
        ``signature``.
        SSL-check dedup: repeated failures of the same host:port probe group
        into ONE incident, while failures of a different port (different
        signature) start their own.

        NOTE: this reads the Incident's *own* ``incident_signature`` column,
        not the member alerts' signatures. The unique partial index
        ``uq_incidents_open_signature`` is what makes "one open incident per
        (tenant, signature)" a hard database invariant rather than an
        application best-effort lookup, so identical probes can never open
        two open incidents concurrently.
        """
        stmt = (
            select(Incident)
            .where(
                Incident.tenant_id == tenant_id,
                Incident.incident_signature == signature,
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

    async def list_stale_open(self, before: datetime, *, limit: int = 500) -> list[Incident]:
        """Open/acknowledged/… incidents with no new alert since ``before``
        (oldest first), for the stale-close lifecycle job."""
        stmt = (
            select(Incident)
            .where(
                Incident.status.in_(
                    ("open", "acknowledged", "investigating", "monitoring")
                ),
                Incident.last_alert_at < before,
                Incident.is_deleted.is_(False),
            )
            .order_by(Incident.last_alert_at.asc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def close_stale(self, incident: Incident, *, reason: str) -> Incident:
        """Auto-close a stale incident, recording why (closure_reason)."""
        incident.status = "closed"
        incident.closed_at = datetime.now(timezone.utc)
        incident.closure_reason = reason
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
        """``(active, correlated)`` incident counts, index-backed.

        The previous single ``COUNT(*) FILTER`` scan over the whole tenant took
        ~36.8s on the 2.7M-incident tenant because the ``correlated`` filter reads
        the un-indexed ``alert_count`` column for every row. ``active`` now reuses
        :meth:`active_counts` (one index-only scan over
        ``ix_incidents_tenant_status_severity_active``), and ``correlated`` is
        served by the partial index ``ix_incidents_tenant_open_correlated``
        (``(tenant_id) WHERE status = 'open' AND alert_count > 1 AND
        is_deleted IS false``, migration d7f2e9a1c4b6).

        Definitions are unchanged:
          * ``active``     = open + acknowledged + investigating
          * ``correlated`` = open incidents with more than one member alert
        The field name and its meaning are unchanged.
        """
        active, _critical = await self.active_counts(
            tenant_id, statuses=["open", "acknowledged", "investigating"]
        )
        correlated = await self._correlated_count(tenant_id)
        return active, correlated

    async def _correlated_count(self, tenant_id: uuid.UUID) -> int:
        """Open incidents with more than one member alert (index-only scan).

        Every predicate below must stay present and spelled exactly as the
        partial index ``ix_incidents_tenant_open_correlated`` declares them, so
        PostgreSQL can satisfy this count from the index without a heap scan
        (see migration d7f2e9a1c4b6).
        """
        stmt = (
            select(func.count())
            .select_from(Incident)
            .where(
                Incident.tenant_id == tenant_id,
                Incident.status == "open",
                Incident.alert_count > 1,
                Incident.is_deleted.is_(False),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def list(
        self,
        tenant_id: uuid.UUID,
        *,
        site_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        status: str | None = None,
        statuses: list[str] | None = None,
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
        if statuses is not None:
            stmt = stmt.where(Incident.status.in_(statuses))
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
        statuses: list[str] | None = None,
        opened_since: datetime | None = None,
        resolved_since: datetime | None = None,
    ) -> int:
        """Count matching incidents.

        SPRINT P1 additive filters (all optional, ``None`` = no filter):
        ``statuses`` matches a set (the active set is open + acknowledged +
        investigating), ``opened_since`` bounds ``opened_at`` (incident
        volume), ``resolved_since`` bounds ``resolved_at`` (resolved work — a
        row with ``resolved_at IS NULL`` never matches a range, which is
        exactly right).
        """
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
        if statuses is not None:
            stmt = stmt.where(Incident.status.in_(statuses))
        if opened_since is not None:
            stmt = stmt.where(Incident.opened_at >= opened_since)
        if resolved_since is not None:
            stmt = stmt.where(Incident.resolved_at >= resolved_since)
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def active_counts(
        self, tenant_id: uuid.UUID, *, statuses: list[str]
    ) -> tuple[int, int]:
        """``(active_total, critical_active)`` in ONE pass (SPRINT P1.1).

        The Command Center needs both how many incidents are in the active set
        AND how many of those are ``critical``. The critical count is a strict
        SUBSET of the active count, so both are read here in a single aggregate
        with a ``FILTER`` clause rather than by two overlapping scans of the
        same rows.

        Measured on a tenant with ~2.7M incidents: 1,560 ms for both numbers
        versus 3,336 ms for the two separate :meth:`count` calls, returning
        IDENTICAL values (2,701,401 / 1,081,593). This is one scan reused - not
        an estimate, and no number is approximated or fabricated.

        Backed by ``ix_incidents_tenant_status_severity_active``
        ``(tenant_id, status, severity) WHERE is_deleted IS false`` (migration
        a4c7e2f91b36), which lets PostgreSQL satisfy both counts from an
        index-only scan instead of a Parallel Seq Scan over the whole heap.
        NOTE the index predicate is spelled ``IS false`` to match what this
        module's ``is_deleted.is_(False)`` emits - a ``= false`` predicate
        would silently never be used. See that migration for the measurement.
        Tenant filtering stays in SQL: ``tenant_id`` is a bound predicate here
        and the leading column of that index.
        """
        stmt = select(
            func.count(),
            func.count().filter(Incident.severity == "critical"),
        ).where(
            Incident.tenant_id == tenant_id,
            Incident.is_deleted.is_(False),
            Incident.status.in_(statuses),
        )
        active, critical = (await self.db.execute(stmt)).one()
        return int(active), int(critical)

    async def hourly_counts(
        self, tenant_id: uuid.UUID, *, since: datetime
    ) -> dict[datetime, int]:
        """``{hour: incidents opened}`` for the window, in ONE GROUP BY (P1).

        The incident trend series — buckets are truncations of ``opened_at``,
        the persisted moment the incident first existed.
        """
        hour = func.date_trunc("hour", Incident.opened_at)
        stmt = (
            select(hour, func.count())
            .where(
                Incident.tenant_id == tenant_id,
                Incident.is_deleted.is_(False),
                Incident.opened_at >= since,
            )
            .group_by(hour)
        )
        return {bucket: count for bucket, count in (await self.db.execute(stmt)).all()}

    async def mean_resolution_minutes(
        self, tenant_id: uuid.UUID, *, since: datetime
    ) -> float | None:
        """Mean ``resolved_at - opened_at`` (minutes) for incidents RESOLVED in
        the window (SPRINT P1 MTTR).

        Returns ``None`` when no sample exists — MTTR with no resolved
        incidents is N/A, never 0.
        """
        stmt = select(
            func.avg(func.extract("epoch", Incident.resolved_at - Incident.opened_at) / 60.0)
        ).where(
            Incident.tenant_id == tenant_id,
            Incident.is_deleted.is_(False),
            Incident.resolved_at.is_not(None),
            Incident.resolved_at >= since,
        )
        value = (await self.db.execute(stmt)).scalar()
        return float(value) if value is not None else None
