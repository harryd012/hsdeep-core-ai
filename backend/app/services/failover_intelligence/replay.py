"""
Incident time machine — rebuild one failover episode as a single ordered story.

The hard rule here is **provenance**. A replay mixes three very different kinds
of statement and must never blur them:

* ``captured`` — written down while it was happening (the FSM log, the route and
  BGP transitions, the decision engine's own output, the evidence list). This is
  history.
* ``derived``  — HSDEEP's interpretation of captured data (blast radius, phase
  durations, gap detection). True about the record; not itself an observation.
* ``context``  — data joined in from other tables (alerts, incident, SLA
  policies) which is real but has a different collection cadence.

``gaps`` names the signals that are absent, so an empty routing section reads as
"we did not capture routing" rather than "routing was fine". Episodes that began
before a collector was enabled are honestly partial: the timeline is what was
recorded, and it says so.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.infrastructure_intelligence import vocabulary as V

PROVENANCE_CAPTURED = "captured"
PROVENANCE_DERIVED = "derived"
PROVENANCE_CONTEXT = "context"

PROVENANCE_ORDER: tuple[str, ...] = (
    PROVENANCE_CAPTURED, PROVENANCE_DERIVED, PROVENANCE_CONTEXT,
)

#: Alias onto the shared Phase 7 evidence vocabulary, so a consumer that speaks
#: observed/derived/context/simulated (canonical telemetry, discovery evidence,
#: failover evidence items) can read replay entries without a translation layer.
#: ``captured`` IS ``observed``; both keys are emitted so neither vocabulary has
#: to change at the same time.
PROVENANCE_ALIASES: dict[str, str] = {
    PROVENANCE_CAPTURED: V.EVIDENCE_OBSERVED,
    PROVENANCE_DERIVED: V.EVIDENCE_DERIVED,
    PROVENANCE_CONTEXT: V.EVIDENCE_CONTEXT,
}

STATUS_TITLES: dict[str, str] = {
    "detected": "Breach detected",
    "failed_over": "Failover observed",
    "recovering": "Recovery starting",
    "recovered": "Recovery verified",
    "verification_failed": "Recovery NOT verified",
    "aborted": "Stand-down (no path switch)",
}

#: Tie-break order for entries that share a timestamp. The opening detection
#: always leads its own second, and the conclusion always follows the evidence.
KIND_ORDER: tuple[str, ...] = (
    "detection",
    "status",
    "decision",
    "route",
    "bgp",
    "link",
    "impact",
    "evidence",
    "verification",
    "alert",
    "incident",
)


def _parse(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _entry(at, kind, title, provenance, source, detail=None) -> dict[str, Any]:
    stamp = _parse(at)
    return {
        "at": stamp.isoformat() if stamp else (str(at) if at else None),
        "kind": kind,
        "title": title,
        "provenance": provenance,
        # Same statement in the shared Phase 7 vocabulary (captured == observed).
        "evidence_provenance": PROVENANCE_ALIASES.get(provenance, provenance),
        "source": source,
        "detail": detail or {},
    }


def build_timeline(
    event, *, alerts: Any = (), incident: Any = None, now: datetime | None = None,
) -> dict[str, Any]:
    """Ordered timeline for one episode, with provenance on every entry."""
    moment = now or datetime.now(timezone.utc)
    trigger = event.trigger or {}
    decision = event.decision or {}
    entries: list[dict[str, Any]] = []

    entries.append(
        _entry(
            event.detected_at,
            "detection",
            (
                f"Episode #{event.sequence_no} opened: "
                f"{trigger.get('reason') or 'observable failover condition'}"
            ),
            PROVENANCE_CAPTURED,
            "failover_events.trigger",
            {
                "path_failure": trigger.get("path_failure"),
                "path_switched": trigger.get("path_switched"),
                "engine_failover_flag": trigger.get("engine_failover_flag"),
                "critical": trigger.get("critical"),
                "unavailable_paths": trigger.get("unavailable_paths"),
                "breaches": trigger.get("breaches") or [],
                "link_from": event.link_from_name,
                "link_to": event.link_to_name,
            },
        )
    )

    for step in event.transition_log or ():
        status = step.get("status")
        entries.append(
            _entry(
                step.get("at"),
                "status",
                STATUS_TITLES.get(status, f"Status -> {status}"),
                PROVENANCE_CAPTURED,
                "failover_events.transition_log",
                {"status": status, "reason": step.get("reason")},
            )
        )

    if decision:
        entries.append(
            _entry(
                event.detected_at,
                "decision",
                (
                    f"Path decision: {decision.get('selected_path')} "
                    f"(score {decision.get('score')}, {decision.get('decision')})"
                ),
                PROVENANCE_CAPTURED,
                "sdwan.evaluate_paths (authoritative engine output)",
                {
                    "reason": decision.get("reason"),
                    "paths": [
                        {
                            "name": path.get("name"),
                            "score": path.get("score"),
                            "health": path.get("health"),
                            "available": path.get("available"),
                        }
                        for path in (decision.get("paths") or [])
                    ],
                },
            )
        )

    for transition in event.route_transitions or ():
        entries.append(
            _entry(
                transition.get("at") or event.detected_at,
                "route",
                (
                    f"Route {transition.get('label')} "
                    f"{transition.get('field')}: {transition.get('from')} -> "
                    f"{transition.get('to')}"
                ),
                PROVENANCE_CAPTURED,
                "network_routes (captured transition)",
                {
                    "key": transition.get("key"),
                    "field": transition.get("field"),
                    "from": transition.get("from"),
                    "to": transition.get("to"),
                },
            )
        )

    for transition in event.bgp_transitions or ():
        entries.append(
            _entry(
                transition.get("at") or event.detected_at,
                "bgp",
                (
                    f"BGP {transition.get('label')} {transition.get('field')}: "
                    f"{transition.get('from')} -> {transition.get('to')}"
                ),
                PROVENANCE_CAPTURED,
                "bgp_neighbors (captured transition)",
                {
                    "key": transition.get("key"),
                    "field": transition.get("field"),
                    "from": transition.get("from"),
                    "to": transition.get("to"),
                },
            )
        )

    blast = event.blast_radius or {}
    if blast.get("total"):
        entries.append(
            _entry(
                event.failed_over_at or event.detected_at,
                "impact",
                (
                    f"Blast radius: {blast.get('total')} dependent resource(s) "
                    f"({(blast.get('by_severity') or {}).get('critical', 0)} critical)"
                ),
                PROVENANCE_DERIVED,
                "resource_relationships (real topology graph)",
                {
                    "nodes": (blast.get("nodes") or [])[:25],
                    "truncated": blast.get("truncated", False),
                },
            )
        )

    evidence = list(event.evidence or ())
    if evidence:
        supports = [item for item in evidence if item.get("finding") == "supports"]
        contradicts = [
            item for item in evidence if item.get("finding") == "contradicts"
        ]
        entries.append(
            _entry(
                event.failed_over_at or event.detected_at,
                "evidence",
                (
                    f"{len(supports)} supporting, {len(contradicts)} contradicting, "
                    f"{len(evidence) - len(supports) - len(contradicts)} neutral "
                    f"evidence item(s)"
                ),
                PROVENANCE_CAPTURED,
                "failover_events.evidence",
                {"items": evidence[:40]},
            )
        )

    verification = event.verification or None
    if verification:
        entries.append(
            _entry(
                event.recovered_at or event.status_changed_at,
                "verification",
                f"Recovery verification: {verification.get('status', 'unknown')}",
                PROVENANCE_CAPTURED,
                "failover_events.verification",
                {
                    "summary": verification.get("summary"),
                    "checks": verification.get("checks") or [],
                    "trigger": verification.get("trigger"),
                },
            )
        )

    for alert in alerts or ():
        entries.append(
            _entry(
                getattr(alert, "occurred_at", None)
                or getattr(alert, "created_at", None),
                "alert",
                (
                    "Alert: "
                    f"{getattr(alert, 'title', None) or getattr(alert, 'metric_type', 'alert')}"
                ),
                PROVENANCE_CONTEXT,
                "alerts (joined by site and detection window)",
                {
                    "alert_id": str(getattr(alert, "id", "")),
                    "severity": getattr(alert, "severity", None),
                    "status": getattr(alert, "status", None),
                    "device_id": str(getattr(alert, "device_id", "") or "") or None,
                },
            )
        )

    if incident is not None:
        entries.append(
            _entry(
                getattr(incident, "created_at", None),
                "incident",
                (
                    "Incident: "
                    f"{getattr(incident, 'title', None) or getattr(incident, 'id', '')}"
                ),
                PROVENANCE_CONTEXT,
                "incidents (linked by correlation, not created by this layer)",
                {
                    "incident_id": str(getattr(incident, "id", "")),
                    "status": getattr(incident, "status", None),
                    "severity": getattr(incident, "severity", None),
                },
            )
        )

    def _sort_key(item: dict[str, Any]):
        kind = item.get("kind") or ""
        rank = KIND_ORDER.index(kind) if kind in KIND_ORDER else len(KIND_ORDER)
        return (_parse(item.get("at")) or moment, rank)

    entries.sort(key=_sort_key)

    gaps: list[str] = []
    if not event.route_transitions:
        gaps.append("no routing transitions were captured for this episode")
    if not event.bgp_transitions:
        gaps.append("no BGP session transitions were captured for this episode")
    if not blast.get("total"):
        gaps.append("no blast radius is recorded (topology graph had no dependents)")
    if not verification:
        gaps.append("recovery has not been verified yet")
    if not alerts and incident is None:
        gaps.append("no alerts or incident were correlated in this window")

    start = _parse(event.detected_at)
    end = _parse(event.recovered_at) or _parse(event.status_changed_at)
    phases: list[dict[str, Any]] = []
    previous_status = None
    previous_at = start
    for step in event.transition_log or ():
        stamp = _parse(step.get("at"))
        if stamp is None or previous_at is None:
            continue
        phases.append(
            {
                "from": previous_status,
                "to": step.get("status"),
                "started_at": previous_at.isoformat(),
                "ended_at": stamp.isoformat(),
                "seconds": round((stamp - previous_at).total_seconds(), 3),
            }
        )
        previous_status = step.get("status")
        previous_at = stamp

    return {
        "timeline": entries,
        "gaps": gaps,
        "phases": phases,
        "coverage": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "duration_seconds": (
                round((end - start).total_seconds(), 3) if start and end else None
            ),
            "still_open": event.is_open,
            "is_partial": bool(gaps),
        },
        "provenance_summary": {
            level: sum(1 for entry in entries if entry["provenance"] == level)
            for level in PROVENANCE_ORDER
        },
    }


# ---------------------------------------------------------------------------
# Persistence-facing wrapper
# ---------------------------------------------------------------------------
class ReplayService:
    """Loads one episode plus its context and builds the timeline."""

    #: Bound on joined alerts so a noisy site cannot make replay expensive.
    ALERT_LIMIT = 50

    def __init__(self, db) -> None:
        from app.repositories.alert_repository import AlertRepository
        from app.repositories.failover_event_repository import FailoverEventRepository
        from app.repositories.incident_repository import IncidentRepository

        self.db = db
        self.event_repo = FailoverEventRepository(db)
        self.alert_repo = AlertRepository(db)
        self.incident_repo = IncidentRepository(db)

    async def reconstruct(
        self, tenant_id, event_id, *, now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Full replay for one episode, scoped to its tenant (None if absent)."""
        event = await self.event_repo.get(event_id, tenant_id)
        if event is None:
            return None

        alerts = []
        try:
            alerts = await self.alert_repo.list(
                tenant_id,
                site_id=event.site_id,
                since=event.detected_at,
                limit=self.ALERT_LIMIT,
            )
        except Exception:  # noqa: BLE001 - replay must work without alert context
            alerts = []

        incident = None
        if event.incident_id is not None:
            incident = await self.incident_repo.get(event.incident_id, tenant_id)

        built = build_timeline(event, alerts=alerts, incident=incident, now=now)
        return {
            "event_id": str(event.id),
            "season": {"scope_key": event.scope_key, "sequence_no": event.sequence_no},
            "status": event.status,
            "site_id": str(event.site_id) if event.site_id else None,
            "edge_device_id": (
                str(event.edge_device_id) if event.edge_device_id else None
            ),
            "link_from": event.link_from_name,
            "link_to": event.link_to_name,
            "status_changed_at": (
                event.status_changed_at.isoformat() if event.status_changed_at else None
            ),
            **built,
        }
