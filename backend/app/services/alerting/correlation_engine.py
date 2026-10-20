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
    its `last_alert_at` is within the correlation window - an old, cold
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
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.alert import Alert
from app.models.incident import Incident
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.alerting.severity_mapper import max_severity
from app.services.alerting.signature import build_signature

# How recently an incident must have had activity to still be considered
# "open for correlation" - an incident that's gone quiet for longer than
# this starts a fresh incident instead of reopening a stale one. Configurable
# via Settings.correlation_inactivity_minutes (default 30).
def _correlation_window() -> timedelta:
    return timedelta(minutes=get_settings().correlation_inactivity_minutes)


# Backward-compatible alias for callers/tests that referenced the old constant.
# The engine itself reads _correlation_window() so config changes take effect
# per-call; this module-level value is only a default-sized snapshot.
CORRELATION_WINDOW = _correlation_window()


def _is_open_signature_conflict(exc: IntegrityError) -> bool:
    """True when ``exc`` is the ``uq_incidents_open_signature`` unique-index violation.

    Only this specific constraint is swallowed and converted into a re-lookup of
    the existing open incident. Every other ``IntegrityError`` (FK violation,
    NOT NULL violation, a different unique index, ...) must propagate unchanged
    so database failures are never hidden.

    Detection uses the asyncpg driver's structured error fields where available
    (``UniqueViolationError`` / SQLSTATE 23505 + constraint name), falling back
    to message-text inspection for other drivers.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    # Identify unique_violation (SQLSTATE 23505) regardless of the exact
    # exception class the driver/dialect surfaces (asyncpg UniqueViolationError,
    # a dialect-wrapped IntegrityError, ...). Check both the exception itself
    # and its .orig for the sqlstate/pgcode.
    sqlstate = str(
        getattr(exc, "pgcode", None)
        or getattr(exc, "sqlstate", None)
        or getattr(orig, "sqlstate", None)
        or getattr(orig, "pgcode", None)
        or ""
    )
    if sqlstate != "23505":
        return False
    # Match the specific constraint by name in either the structured field
    # or the message text.
    constraint = (
        getattr(orig, "constraint", None)
        or getattr(orig, "constraint_name", None)
        or ""
    )
    if constraint == "uq_incidents_open_signature":
        return True
    return "uq_incidents_open_signature" in str(orig) or "uq_incidents_open_signature" in str(exc)

# Category that qualifies an alert as a root-cause candidate. Kept as a
# single constant (not a list) deliberately: broadening this later is a
# one-line change, not a design change.
_ROOT_CAUSE_CATEGORY = "availability"


@dataclass(frozen=True)
class SignalEvidence:
    """One explainable evidence signal behind a correlation decision.

    ``strength`` is one of: strong | moderate | weak. The ordering is
    intentionally narrow so callers can assert exact expected values in
    tests and so the confidence model stays deterministic.
    """

    signal_type: str
    strength: str
    detail: str


@dataclass
class CorrelationResult:
    """Canonical correlation result for one correlated-alert group.

    Contract (per the P2.1 brief):
      - correlation_id: stable identity of the correlated group.
      - tenant_id: group membership is tenant-scoped; never mixed.
      - alert_ids: member alerts of the group.
      - primary_resource / related_resources: device/site resources.
      - correlation_signals: explainable evidence behind the decision.
      - confidence (0-1) and score (0-100): deterministic from signals.
      - first_seen / last_seen / status: incident state used downstream.
      - reason: human-readable explanation of the decision.
    """

    incident: Incident | None
    is_new_incident: bool
    correlation_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    alert_ids: list[uuid.UUID]
    primary_resource: dict | None
    related_resources: list[dict]
    correlation_signals: list[SignalEvidence]
    confidence: float
    score: int
    first_seen: datetime | None
    last_seen: datetime | None
    status: str | None
    reason: str
    is_root_cause: bool
    is_suppressed: bool
    recomputed: bool


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
            return CorrelationResult(
                incident=None,
                is_new_incident=False,
                correlation_id=None,
                tenant_id=alert.tenant_id,
                alert_ids=[],
                primary_resource=None,
                related_resources=[],
                correlation_signals=[],
                confidence=0.0,
                score=0,
                first_seen=None,
                last_seen=None,
                status=None,
                reason="no device_id or site_id - left uncorrelated",
                is_root_cause=False,
                is_suppressed=False,
                recomputed=False,
            )

        # Probe dedup signature (migration 1be605e3f990's alert_signature
        # column): repeated failures of the SAME probe (same metric type and
        # title, e.g. one host:port SSL check) group into ONE incident,
        # while a different probe on the same device starts its own. Falls
        # back to device/site-level grouping for alerts without a usable
        # signature.
        signature = self._signature(alert)
        if signature is not None:
            await self.alert_repo.set_alert_signature(alert, signature)
            incident = await self.incident_repo.get_open_for_signature(signature, alert.tenant_id)
            if incident is not None and incident.last_alert_at < (
                datetime.now(timezone.utc) - _correlation_window()
            ):
                incident = None
        else:
            incident = await self._find_open_incident(alert, alert.tenant_id)
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
            try:
                # Use a SAVEPOINT (when backed by a real DB session) so a
                # unique-index conflict only undoes the incident INSERT -
                # prior work in this transaction (the alert row flushed
                # earlier) is preserved and the session stays usable without
                # a full rollback. In-memory fake repos (no .db attribute)
                # skip the savepoint - their create() never raises
                # IntegrityError from a real unique index.
                db = getattr(self.incident_repo, "db", None)
                if db is not None:
                    async with db.begin_nested():
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
                            incident_signature=signature,
                        )
                else:
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
                        incident_signature=signature,
                    )
            except IntegrityError as exc:
                # Concurrent transaction won the unique-index race: another
                # caller already created the qualifying open incident for this
                # (tenant, signature). The savepoint was rolled back above;
                # re-query using tenant_id + incident_signature and join that
                # incident through the normal recurrence/severity path.
                if not _is_open_signature_conflict(exc):
                    raise
                incident = await self.incident_repo.get_open_for_signature(
                    signature, alert.tenant_id
                )
                if incident is None:
                    # The winner's commit should have made the row visible;
                    # if not, something unexpected happened - re-raise rather
                    # than silently creating a duplicate.
                    raise
                is_new_incident = False
        elif already_member and alert.severity == incident.severity:
            # Idempotent recurrence: the alert is already a member of this
            # incident and its severity is unchanged, so nothing about the
            # incident's aggregate state changes - the occurrence is
            # re-recorded below with the counters advanced, the recompute
            # skipped, and the result marked not-recomputed.
            pass
        else:
            await self.incident_repo.add_alert(incident, occurred_at=alert.last_occurred_at)

        if already_member and alert.severity == incident.severity:
            # Idempotent recurrence: the alert is already a member and its
            # severity is unchanged, so skip reloading up to 1000 members,
            # re-deriving root cause, re-applying suppression, and re-running
            # priority scoring.
            await self.incident_repo.touch_occurrence(
                incident, occurred_at=alert.last_occurred_at
            )
            recomputed = False
        else:
            recomputed = True

        # Correlation ID: stable per-incident grouping id, equal to the
        # incident's id on first creation and inherited by every alert that
        # joins (no merge operation exists yet to change it).
        correlation_id = incident.id

        # Link the alert to the incident BEFORE computing the member set, so a
        # brand-new incident's only member (this alert) is included and a new
        # joiner appears alongside the pre-existing members.
        await self.alert_repo.set_correlation_fields(
            alert, correlation_id=correlation_id, incident_id=incident.id
        )

        if recomputed:
            member_alerts = await self.alert_repo.list(
                alert.tenant_id, incident_id=incident.id, status=None, limit=1000
            )
        else:
            member_alerts = [alert]

        # Recompute severity/root-cause over every member alert (including
        # this one, now persisted above) rather than just comparing this one
        # alert's severity against the incident's - a resolved member alert
        # should stop counting toward the incident's severity.
        active_members = [a for a in member_alerts if a.status != "resolved"]
        new_severity = max_severity([a.severity for a in active_members]) if active_members else alert.severity
        if new_severity != incident.severity:
            await self.incident_repo.update_severity(incident, new_severity)

        root_cause = self._select_root_cause(member_alerts)
        await self.incident_repo.set_root_cause(incident, root_cause.id if root_cause else None)

        is_root_cause = root_cause is not None and root_cause.id == alert.id
        await self._apply_root_cause_and_suppression(member_alerts, root_cause)

        alert_ids = sorted({a.id for a in member_alerts})
        primary_resource = self._primary_resource(member_alerts, alert, incident)
        related_resources = self._related_resources(member_alerts, alert)
        correlation_signals = self._correlation_signals(member_alerts, alert, incident)
        score = self._signal_points(correlation_signals)
        confidence = self._confidence(score)
        reason = self._reason(correlation_signals, is_new_incident, incident)

        return CorrelationResult(
            incident=incident,
            is_new_incident=is_new_incident,
            correlation_id=correlation_id,
            tenant_id=alert.tenant_id,
            alert_ids=alert_ids,
            primary_resource=primary_resource,
            related_resources=related_resources,
            correlation_signals=correlation_signals,
            confidence=confidence,
            score=score,
            first_seen=incident.opened_at if incident else None,
            last_seen=incident.last_alert_at if incident else None,
            status=incident.status if incident else None,
            reason=reason,
            is_root_cause=is_root_cause,
            is_suppressed=alert.is_suppressed,
            recomputed=recomputed,
        )

    async def _find_open_incident(self, alert: Alert, tenant_id: uuid.UUID) -> Incident | None:
        cutoff = datetime.now(timezone.utc) - _correlation_window()
        incident: Incident | None = None
        if alert.device_id is not None:
            if alert.sensor_id is not None:
                # Per-probe correlation: an SSL-check failure on port 443 must
                # not merge into an open SSL-check incident for port 993 on
                # the same device. Same-sensor incidents group; different
                # sensors on one device stay separate. No site-level fallback
                # for sensor-scoped alerts - that would defeat the split.
                incident = await self.incident_repo.get_open_for_device_and_sensor(
                    alert.device_id, alert.sensor_id, tenant_id
                )
            else:
                incident = await self.incident_repo.get_open_for_device(alert.device_id, tenant_id)
        if incident is None and alert.sensor_id is None and alert.site_id is not None:
            incident = await self.incident_repo.get_open_for_site(alert.site_id, tenant_id)
        if incident is not None and incident.last_alert_at < cutoff:
            # Stale (Time-window correlation rule) - treat as if none found,
            # so a fresh incident starts instead of reopening a cold one.
            return None
        return incident

    @staticmethod
    def _signature(alert: Alert) -> str | None:
        """Normalized probe dedup key (see app.services.alerting.signature).

        Includes device/sensor/signal identity so two devices with the same
        title never merge, and never emits the literal "None" prefix for a
        NULL metric_type."""
        return build_signature(
            metric_type=alert.metric_type,
            title=alert.title,
            device_id=alert.device_id,
            sensor_id=alert.sensor_id,
            signal_external_id=alert.signal_external_id,
        )

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

    # ------------------------------------------------------------------
    # Deterministic, explainable correlation evidence (P2.1)
    # ------------------------------------------------------------------

    @staticmethod
    def _correlation_signals(
        member_alerts: list[Alert], alert: Alert, incident: Incident
    ) -> list[SignalEvidence]:
        """Deterministic, explainable evidence behind this correlation decision.

        Signal strength vocabulary is intentionally narrow (strong | moderate)
        so callers can assert exact values and the confidence model stays
        deterministic. Only real stored fields are used - never a fabricated
        relationship or a guessed grouping key.
        """
        signals: list[SignalEvidence] = []
        if alert.device_id is not None:
            signals.append(
                SignalEvidence(
                    signal_type="same_device",
                    strength="strong",
                    detail=f"alerts share device {alert.device_id}",
                )
            )
        elif alert.site_id is not None:
            signals.append(
                SignalEvidence(
                    signal_type="same_site",
                    strength="moderate",
                    detail=f"alerts share site {alert.site_id}",
                )
            )
        if incident is not None and incident.last_alert_at is not None:
            if incident.last_alert_at >= datetime.now(timezone.utc) - _correlation_window():
                signals.append(
                    SignalEvidence(
                        signal_type="temporal_proximity",
                        strength="moderate",
                        detail="incident activity within correlation window",
                    )
                )
        return signals

    @staticmethod
    def _signal_points(signals: list[SignalEvidence]) -> int:
        """Deterministic 0-100 score: strong=30, moderate=20, weak=10 each."""
        points = {"strong": 30, "moderate": 20, "weak": 10}
        return sum(points.get(s.strength, 0) for s in signals)

    @staticmethod
    def _confidence(score: int) -> float:
        """Map the 0-100 score to a 0-1 confidence, capped at 1.0."""
        return min(max(score, 0) / 100.0, 1.0)

    @staticmethod
    def _primary_resource(
        member_alerts: list[Alert], alert: Alert, incident: Incident
    ) -> dict | None:
        """The primary device/site resource anchoring this correlation group."""
        if alert.device_id is not None:
            return {"type": "device", "id": str(alert.device_id)}
        if alert.site_id is not None:
            return {"type": "site", "id": str(alert.site_id)}
        return None

    @staticmethod
    def _related_resources(member_alerts: list[Alert], alert: Alert) -> list[dict]:
        """Distinct device/site resources (other than the primary) in the group."""
        resources: list[dict] = []
        seen: set[str] = set()
        for a in member_alerts:
            if a.device_id is not None and a.device_id != alert.device_id:
                key = f"device:{a.device_id}"
                if key not in seen:
                    resources.append({"type": "device", "id": str(a.device_id)})
                    seen.add(key)
            elif a.site_id is not None and a.site_id != alert.site_id:
                key = f"site:{a.site_id}"
                if key not in seen:
                    resources.append({"type": "site", "id": str(a.site_id)})
                    seen.add(key)
        return resources

    @staticmethod
    def _reason(
        signals: list[SignalEvidence], is_new_incident: bool, incident: Incident
    ) -> str:
        signal_desc = ", ".join(s.signal_type for s in signals) or "no grouping key"
        if is_new_incident:
            return f"new incident opened via {signal_desc}"
        return f"joined via {signal_desc}"
