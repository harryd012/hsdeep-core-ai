"""
Alert Correlation Engine.

Groups related Alerts into Incidents and identifies a root-cause candidate,
using only real, already-stored data (Device/Interface/Site/Alert rows) -
same "honest heuristic" philosophy as `AIInsightService`/`EventClassifier`:
every rule below is a deterministic query/comparison, never ML or a
simulated score.

Correlation rules implemented (per the sprint brief):
  - Duplicate alert detection: NOT reimplemented here - already solved at
    the DB level by Alert's partial unique indexes
    (uq_alerts_active_per_sensor / uq_alerts_active_per_signal) and
    AlertRepository.record_occurrence(). This engine only runs for alerts
    that are genuinely new/distinct rows.
  - Device-based grouping: alerts sharing a `device_id` within the
    correlation time window join the same Incident.
  - Site-based grouping: alerts with no `device_id` but a shared `site_id`
    join the same Incident (e.g. a vendor-native alert not yet linked to a
    Device).
  - Interface dependency correlation: covered by device-based grouping,
    since an interface-scoped sensor's alert already carries the parent
    Device's `device_id` (Sensor.device_id) - no separate interface-keyed
    lookup is needed to catch "this interface's alert belongs with this
    device's incident".
  - Time-window correlation: an existing open Incident is only joined if
    its `last_alert_at` is within `CORRELATION_WINDOW` - an old, cold
    incident does not silently absorb an unrelated new alert.
  - Parent-child alert relationships: the incident's root-cause alert (if
    confidently identified) becomes `parent_alert_id` for every other
    member alert.
  - Multi-source correlation readiness: grouping keys off `device_id`/
    `site_id` (vendor-agnostic, denormalized columns), never off
    `monitoring_source_id` or a vendor-specific field - two different
    vendors' collectors reporting on the same Device already correlate
    correctly today, with no per-vendor code in this engine.

Root-cause heuristic: within an incident, the earliest-triggered
"availability"-category alert (a device/link being down, as opposed to a
performance/capacity degradation) is the most likely originating cause.
If no availability-category alert exists yet, the incident has no
confident root cause and nothing is suppressed - this is a deliberately
conservative heuristic (false negatives over false positives): NOT
suppressing an alert is always safe (it just shows up separately), while
wrongly suppressing a genuinely independent alert would hide real signal.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.alert import Alert
from app.models.incident import Incident
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.alerting.severity_mapper import max_severity

# How recently an incident must have had activity to still be considered
# "open for correlation" - an incident that's gone quiet for longer than
# this starts a fresh incident instead of reopening a stale one.
CORRELATION_WINDOW = timedelta(minutes=30)

# Category that qualifies an alert as a root-cause candidate. Kept as a
# single constant (not a list) deliberately: broadening this later is a
# one-line change, not a design change.
_ROOT_CAUSE_CATEGORY = "availability"


@dataclass
class CorrelationResult:
    incident: Incident | None
    is_new_incident: bool
    is_root_cause: bool
    is_suppressed: bool
    # False when the incident's aggregate state (member set, severity,
    # root cause, suppression) is already correct for this alert and no
    # recompute was needed - i.e. an idempotent recurrence of an unchanged
    # condition. Callers use this to skip re-running priority scoring and
    # writing a redundant "correlated" history entry for every poll.
    recomputed: bool = True


class CorrelationEngine:
    def __init__(self, alert_repo: AlertRepository, incident_repo: IncidentRepository):
        self.alert_repo = alert_repo
        self.incident_repo = incident_repo

    async def correlate(self, alert: Alert) -> CorrelationResult:
        """Assigns `alert` to an Incident (existing or newly created),
        recomputes that incident's severity/root-cause, and applies the
        suppression rule to every other member alert. Returns what
        happened so the caller (AlertService) can write an AlertHistory
        entry with real details rather than guessing.

        Only ever called for alerts with a `device_id` or `site_id` -
        alerts with neither (no denormalized location at all) have no
        grouping key and are left uncorrelated (correlation_id/incident_id
        stay None) rather than being force-grouped under a meaningless
        "everything with no location" bucket.
        """
        if alert.device_id is None and alert.site_id is None:
            return CorrelationResult(incident=None, is_new_incident=False, is_root_cause=False, is_suppressed=False)

        # Probe dedup signature (migration 1be605e3f990's alert_signature
        # column): repeated failures of the SAME probe (same metric type and
        # title, e.g. one host:port SSL check) group into ONE incident,
        # while a different probe on the same device starts its own. Falls
        # back to device/site-level grouping for alerts without a usable
        # signature.
        signature = self._signature(alert)
        if signature is not None:
            await self.alert_repo.set_alert_signature(alert, signature)
            incident = await self.incident_repo.get_open_for_signature(signature)
            if incident is not None and incident.last_alert_at < (
                datetime.now(timezone.utc) - CORRELATION_WINDOW
            ):
                incident = None
        else:
            incident = await self._find_open_incident(alert)
        is_new_incident = incident is None

        # Idempotent-recurrence detection (Issue: repeated monitoring events
        # must NOT re-create/duplicate an equivalent correlation or re-run the
        # full member-alert recompute for every identical poll). If this exact
        # alert is ALREADY a member of the very incident we just matched, and
        # its severity is unchanged from the incident's current severity, then
        # nothing about the incident's aggregate state changes by re-recording
        # the occurrence: we advance the recurrence counters / last_alert_at
        # only and skip reloading up to 1000 member alerts, re-deriving root
        # cause, re-applying suppression, and re-running priority scoring.
        already_member = (not is_new_incident) and alert.incident_id == incident.id

        if incident is None:
            incident = await self.incident_repo.create(
                tenant_id=alert.tenant_id,
                site_id=alert.site_id,
                device_id=alert.device_id,
                title=self._build_incident_title(alert),
                description=None,
                severity=alert.severity,
                status="open",
                opened_at=alert.triggered_at,
                last_alert_at=alert.triggered_at,
                alert_count=1,
            )
        elif already_member and alert.severity == incident.severity:
            await self.incident_repo.touch_occurrence(
                incident, occurred_at=alert.last_occurred_at
            )
            correlation_id = incident.id
            await self.alert_repo.set_correlation_fields(
                alert, correlation_id=correlation_id, incident_id=incident.id
            )
            return CorrelationResult(
                incident=incident,
                is_new_incident=False,
                is_root_cause=False,
                is_suppressed=alert.is_suppressed,
                recomputed=False,
            )
        else:
            await self.incident_repo.add_alert(incident, occurred_at=alert.last_occurred_at)

        # Correlation ID: stable per-incident grouping id, distinct from
        # incident.id itself so a future "merge two incidents" operation
        # (not implemented here - out of scope) could keep correlation_id
        # stable across an incident_id change. For now it's set equal to
        # the incident's id on first creation and inherited by every alert
        # that joins - simplest correct implementation given no merge
        # operation exists yet.
        correlation_id = incident.id

        await self.alert_repo.set_correlation_fields(
            alert, correlation_id=correlation_id, incident_id=incident.id
        )

        # Recompute severity/root-cause over every member alert (including
        # this one, already persisted above) rather than just comparing
        # this one alert's severity against the incident's - a resolved
        # member alert should stop counting toward the incident's severity.
        member_alerts = await self.alert_repo.list(
            alert.tenant_id, incident_id=incident.id, status=None, limit=1000
        )
        active_members = [a for a in member_alerts if a.status != "resolved"]

        new_severity = max_severity([a.severity for a in active_members]) if active_members else alert.severity
        if new_severity != incident.severity:
            await self.incident_repo.update_severity(incident, new_severity)

        root_cause = self._select_root_cause(member_alerts)
        await self.incident_repo.set_root_cause(incident, root_cause.id if root_cause else None)

        is_root_cause = root_cause is not None and root_cause.id == alert.id
        await self._apply_root_cause_and_suppression(member_alerts, root_cause)

        return CorrelationResult(
            incident=incident,
            is_new_incident=is_new_incident,
            is_root_cause=is_root_cause,
            is_suppressed=alert.is_suppressed,
            recomputed=True,
        )

    async def _find_open_incident(self, alert: Alert) -> Incident | None:
        cutoff = datetime.now(timezone.utc) - CORRELATION_WINDOW
        incident: Incident | None = None
        if alert.device_id is not None:
            if alert.sensor_id is not None:
                # Per-probe correlation: an SSL-check failure on port 443 must
                # not merge into an open SSL-check incident for port 993 on
                # the same device. Same-sensor incidents group; different
                # sensors on one device stay separate. No site-level fallback
                # for sensor-scoped alerts - that would defeat the split.
                incident = await self.incident_repo.get_open_for_device_and_sensor(
                    alert.device_id, alert.sensor_id
                )
            else:
                incident = await self.incident_repo.get_open_for_device(alert.device_id)
        if incident is None and alert.sensor_id is None and alert.site_id is not None:
            incident = await self.incident_repo.get_open_for_site(alert.site_id)
        if incident is not None and incident.last_alert_at < cutoff:
            # Stale (Time-window correlation rule) - treat as if none found,
            # so a fresh incident starts instead of reopening a cold one.
            return None
        return incident

    @staticmethod
    def _signature(alert: Alert) -> str | None:
        """Probe dedup key: "<metric_type>:<title>". Titles carry the probe
        identity for SSL checks ("SSL Security Check - host:port"), so the
        same host:port always maps to one signature while other ports map
        to their own. Alerts without a title have no stable signature."""
        if not alert.title:
            return None
        return f"{alert.metric_type}:{alert.title}"

    @staticmethod
    def _build_incident_title(alert: Alert) -> str:
        return f"Incident: {alert.title}"

    @staticmethod
    def _select_root_cause(member_alerts: list[Alert]) -> Alert | None:
        """Earliest-triggered active alert whose category is the
        root-cause category, or None if no such alert exists yet among
        currently-active members."""
        candidates = [
            a for a in member_alerts if a.category == _ROOT_CAUSE_CATEGORY and a.status != "resolved"
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda a: a.triggered_at)

    async def _apply_root_cause_and_suppression(
        self, member_alerts: list[Alert], root_cause: Alert | None
    ) -> None:
        for member in member_alerts:
            if member.status == "resolved":
                # Never touch a resolved alert's root-cause/suppression
                # flags retroactively - its history is settled.
                continue
            is_this_root_cause = root_cause is not None and member.id == root_cause.id
            should_suppress = root_cause is not None and not is_this_root_cause
            if member.is_root_cause_candidate != is_this_root_cause or member.is_suppressed != should_suppress:
                parent_id = root_cause.id if (root_cause is not None and not is_this_root_cause) else None
                await self.alert_repo.set_correlation_fields(
                    member,
                    correlation_id=member.correlation_id,
                    incident_id=member.incident_id,
                    parent_alert_id=parent_id,
                    is_root_cause_candidate=is_this_root_cause,
                    is_suppressed=should_suppress,
                )
