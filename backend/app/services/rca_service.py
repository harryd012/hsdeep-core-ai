"""
RCA (Root Cause Analysis) Service — Event Listener.

Subscribes to AI_RCA_STARTED, pulls the incident's correlated alerts,
produces a root-cause summary and candidate SOP recommendation, persists
the results on the Incident row, and publishes RCA_COMPLETED so the
downstream SOP-selection flow continues.

Since no LLM/ollama integration exists in the codebase yet, the root-cause
summary is produced by a deterministic rule-based analyser over the real
Alert/Sensor/Device data. The `RootCauseAnalyser` protocol is designed so
that a future LLM-backed implementation can be swapped in without changing
the listener wiring or the event publishing logic.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from app.models.alert import Alert
from app.models.incident import Incident
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.events.event_bus import EventBus, EventType, DomainEvent
from app.services.dependency_intelligence.integration import (
    DependencyEnrichedRootCauseAnalyser,
)
from app.services.sop.recommendation_engine import SOPRecommendationEngine

logger = logging.getLogger(__name__)

# A session factory returns an async context manager yielding an AsyncSession
# (AsyncSessionLocal is one such factory). Mirrors
# AutonomousLifecycleOrchestrator's AsyncSessionFactory so RCA can open a
# FRESH session per event — the production wiring in app/main.py no longer
# holds a stale startup session across event handler invocations.
AsyncSessionFactory = Callable[[], Any]


# ---------------------------------------------------------------------------
# Protocol / dataclass interfaces
# ---------------------------------------------------------------------------

@dataclass
class RootCauseResult:
    """Output of a root-cause analysis pass."""
    root_cause_summary: str
    supporting_evidence: list[dict[str, Any]]
    recommended_sop_id: uuid.UUID | str | None = None
    recommended_sop_name: str | None = None
    requires_approval: bool = False


class RootCauseAnalyser(Protocol):
    """Pluggable interface for producing a root-cause analysis.

    The default implementation is rule-based (no LLM). A future
    `OllamaRootCauseAnalyser` can implement this same protocol and be
    injected without touching ``RCAService``.
    """

    async def analyse(self, incident: Incident, alerts: list[Alert]) -> RootCauseResult: ...


# ---------------------------------------------------------------------------
# Default rule-based analyser
# ---------------------------------------------------------------------------

class RuleBasedRootCauseAnalyser:
    """Deterministic, rule-based root-cause analysis over real Alert rows.

    Strategy:
    1. Pick the root-cause candidate alert (``is_root_cause_candidate`` first,
       then the highest-severity, oldest alert).
    2. Build ``root_cause_summary`` from the alert's title, description, and
       device/category context.
    3. Collect ``supporting_evidence`` from all member alerts (id, title,
       severity, why it's considered supporting).
    4. Use ``SOPRecommendationEngine`` to pick the best-matching SOP; if
       that returns nothing, fall back to the incident's
       ``recommended_sop`` (set earlier by ``DecisionEngine``).
    """

    def __init__(
        self,
        sop_recommendation_engine: SOPRecommendationEngine | None = None,
    ):
        self._sop_engine = sop_recommendation_engine
        # An explicitly injected engine is authoritative and never replaced.
        self._sop_engine_injected = sop_recommendation_engine is not None
        self._sop_session = None

    def bind_session(self, session) -> None:
        """Build a real ``SOPRecommendationEngine`` on the caller's session.

        Without this, ``_sop_engine`` is None in production and the existing SOP
        recommendation step in ``analyse()`` never runs. The engine is rebuilt
        whenever a DIFFERENT session is bound: RCAService opens a fresh session
        per event, so caching one would silently reuse a closed session (the same
        stale-session hazard documented in autonomous_lifecycle.py). Imports are
        lazy so this module's import surface is unchanged.
        """
        if session is None or self._sop_engine_injected:
            return
        if session is self._sop_session:
            return  # already bound to exactly this session
        self._sop_session = session
        from app.repositories.alert_repository import AlertRepository
        from app.repositories.business_service_repository import BusinessServiceRepository
        from app.repositories.device_repository import DeviceRepository
        from app.repositories.monitoring_source_repository import MonitoringSourceRepository
        from app.repositories.runbook_execution_repository import (
            RunbookExecutionRepository,
        )
        from app.repositories.site_repository import SiteRepository
        from app.repositories.sop_repository import SOPRepository
        from app.services.priority.business_impact_engine import BusinessImpactEngine

        devices = DeviceRepository(session)
        sites = SiteRepository(session)
        self._sop_engine = SOPRecommendationEngine(
            sop_repo=SOPRepository(session),
            alert_repo=AlertRepository(session),
            device_repo=devices,
            site_repo=sites,
            monitoring_source_repo=MonitoringSourceRepository(session),
            runbook_execution_repo=RunbookExecutionRepository(session),
            business_impact_engine=BusinessImpactEngine(
                device_repo=devices,
                site_repo=sites,
                business_service_repo=BusinessServiceRepository(session),
            ),
        )

    async def analyse(self, incident: Incident, alerts: list[Alert]) -> RootCauseResult:
        root_cause_alert = self._pick_root_cause(alerts)

        if root_cause_alert is not None:
            summary = self._build_summary(root_cause_alert, incident)
        else:
            summary = self._fallback_summary(incident, alerts)

        evidence = self._build_evidence(alerts, root_cause_alert)

        # SOP recommendation
        recommended_sop_id = None
        recommended_sop_name = None
        requires_approval = False

        if self._sop_engine is not None and root_cause_alert is not None:
            try:
                recommendations = await self._sop_engine.recommend(
                    incident, limit=1, min_confidence=0.0
                )
                if recommendations:
                    rec = recommendations[0]
                    recommended_sop_id = rec.sop_id
                    recommended_sop_name = rec.name
                    requires_approval = rec.requires_approval
            except Exception as exc:
                logger.warning(
                    "SOP recommendation failed during RCA: %s", exc, exc_info=True
                )

        # Fall back to incident.recommended_sop (set by DecisionEngine) if we
        # couldn't get a recommendation from the matching engine.
        if recommended_sop_id is None and incident.recommended_sop:
            recommended_sop_id = incident.recommended_sop

        return RootCauseResult(
            root_cause_summary=summary,
            supporting_evidence=evidence,
            recommended_sop_id=recommended_sop_id,
            recommended_sop_name=recommended_sop_name,
            requires_approval=requires_approval,
        )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _pick_root_cause(alerts: list[Alert]) -> Alert | None:
        """Return the most-likely root-cause alert, or None if no alerts."""
        if not alerts:
            return None
        # Priority: 1) is_root_cause_candidate=True, 2) oldest triggered,
        # 3) highest severity.
        candidates = [a for a in alerts if a.is_root_cause_candidate]
        if candidates:
            return candidates[0]
        # No candidate flagged — pick the oldest active alert.
        active = [a for a in alerts if a.status != "resolved"] or alerts
        return min(active, key=lambda a: a.triggered_at)

    @staticmethod
    def _build_summary(alert: Alert, incident: Incident) -> str:
        parts: list[str] = []
        if alert.device_id:
            parts.append(f"Device {alert.device_id}")
        if alert.category:
            parts.append(f"category '{alert.category}'")
        if alert.title:
            parts.append(f'alert "{alert.title}"')
        if alert.vendor:
            parts.append(f"vendor {alert.vendor}")
        if parts:
            return "Root cause: " + ", ".join(parts) + "."
        return f"Root cause analysis for incident {incident.title or incident.id}."

    @staticmethod
    def _fallback_summary(incident: Incident, alerts: list[Alert]) -> str:
        if alerts:
            first = alerts[0]
            return (
                f'Root cause: "{first.title}" '
                f"(severity={first.severity}, category={first.category or 'unknown'})."
            )
        return f'No alerts correlated for incident "{incident.title or incident.id}".'

    @staticmethod
    def _build_evidence(alerts: list[Alert], root_cause: Alert | None) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for a in alerts:
            reason = "root-cause candidate" if a.is_root_cause_candidate else "correlated alert"
            if root_cause and a.id == root_cause.id:
                reason = "root-cause candidate"
            evidence.append({
                "alert_id": str(a.id),
                "title": a.title,
                "severity": a.severity,
                "category": a.category,
                "status": a.status,
                "reason": reason,
            })
        return evidence


# ---------------------------------------------------------------------------
# Persistence helper — updates incident AI RCA fields via repository
# ---------------------------------------------------------------------------

def _persist_rca_results(
    incident_repo: IncidentRepository,
    incident: Incident,
    result: RootCauseResult,
) -> Incident:
    """Persist root_cause_summary / supporting_evidence / recommended_sop
    onto the incident row. This is a synchronous helper: it only mutates
    the ORM object and stages it via ``AsyncSession.add()`` (a sync method
    in SQLAlchemy's async API). The caller is responsible for the async
    ``flush()`` that actually writes the row."""
    incident.root_cause_summary = result.root_cause_summary
    incident.supporting_evidence = {
        "evidence": result.supporting_evidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if result.recommended_sop_id is not None:
        # Store the sop_id as a string (recommended_sop is a String column)
        incident.recommended_sop = str(result.recommended_sop_id)
    incident_repo.db.add(incident)
    return incident


# ---------------------------------------------------------------------------
# The event-listener service
# ---------------------------------------------------------------------------

class RCAService:
    """Event listener for AI_RCA_STARTED.

    Reads the incident + its correlated alerts, runs the root-cause
    analyser, persists results on the incident, and publishes
    ``RCA_COMPLETED`` with the recommended ``sop_id`` in the payload so
    that ``_on_rca_completed`` downstream has something to act on.
    """

    def __init__(
        self,
        event_bus: EventBus,
        alert_repo: AlertRepository | None = None,
        incident_repo: IncidentRepository | None = None,
        analyser: RootCauseAnalyser | None = None,
        *,
        session_factory: AsyncSessionFactory | None = None,
    ):
        """Construct the RCA listener.

        Two wiring styles:
        - Production (app/main.py): pass ``session_factory`` (e.g.
          ``AsyncSessionLocal``). Each event opens a FRESH session, so the
          handler sees rows committed after startup and a failure rolls back
          cleanly instead of leaking a half-dirty transaction into the next
          incident's handler.
        - Tests / direct use: pass ``alert_repo`` + ``incident_repo`` and own
          the session lifecycle yourself.
        """
        if session_factory is None and (alert_repo is None or incident_repo is None):
            raise ValueError(
                "RCAService requires either session_factory (fresh session per "
                "event) or both alert_repo and incident_repo"
            )
        self.event_bus = event_bus
        self._alert_repo = alert_repo
        self._incident_repo = incident_repo
        self._session_factory = session_factory
        # Production default: the EXISTING rule-based analyser, wrapped so real
        # incidents additionally receive deterministic dependency findings.
        # Additive and failure-isolated (see DependencyEnrichedRootCauseAnalyser):
        # an injected analyser is used as-is, and if dependency analysis fails the
        # existing RCA result is returned untouched.
        self.analyser: RootCauseAnalyser = analyser or DependencyEnrichedRootCauseAnalyser(
            RuleBasedRootCauseAnalyser()
        )

    def register(self) -> None:
        """Subscribe this service's handler to AI_RCA_STARTED."""
        self.event_bus.subscribe(EventType.AI_RCA_STARTED)(self._on_ai_rca_started)

    async def _on_ai_rca_started(self, event: DomainEvent) -> None:
        """Handle AI_RCA_STARTED -> run RCA -> commit -> publish RCA_COMPLETED.

        Session strategy: when a ``session_factory`` was injected, a fresh
        ``AsyncSession`` is opened for THIS event and closed on exit — leaving
        the ``async with`` block rolls back any half-applied transaction, so a
        failed RCA can never leave dirty state in a shared session for the next
        incident's handler. Otherwise (tests / direct use) the caller-supplied
        repos own their own session lifecycle.
        """
        incident_id = event.payload.get("incident_id")

        if not incident_id:
            logger.warning("AI_RCA_STARTED event missing incident_id")
            return

        try:
            if self._session_factory is not None:
                async with self._session_factory() as db:
                    await self._run_rca(
                        event=event,
                        incident_id=incident_id,
                        alert_repo=AlertRepository(db),
                        incident_repo=IncidentRepository(db),
                    )
            else:
                # Unreachable if constructed correctly (__init__ validates),
                # but kept explicit for type-safety.
                alert_repo = self._alert_repo
                incident_repo = self._incident_repo
                if alert_repo is None or incident_repo is None:
                    raise RuntimeError(
                        "RCAService must be constructed with either session_factory "
                        "or both alert_repo and incident_repo"
                    )
                await self._run_rca(
                    event=event,
                    incident_id=incident_id,
                    alert_repo=alert_repo,
                    incident_repo=incident_repo,
                )
        except Exception as exc:
            logger.error(
                "RCA failed for incident %s: %s",
                incident_id,
                exc,
                exc_info=True,
            )

    async def _run_rca(
        self,
        *,
        event: DomainEvent,
        incident_id: str,
        alert_repo: AlertRepository,
        incident_repo: IncidentRepository,
    ) -> None:
        """Execute one RCA pass against the provided repos.

        Split out from ``_on_ai_rca_started`` so the fresh-session path and
        the caller-provided-repo path share identical logic.

        Persistence: the ORM object is mutated, staged via ``db.add()``, then
        flushed AND committed. ``flush()`` alone only emits the UPDATE inside
        the current transaction — without ``commit()`` the write is rolled back
        when the session closes, so the RCA results would never be durable.
        """
        ticket_id = event.payload.get("ticket_id")

        incident = await incident_repo.get(
            uuid.UUID(incident_id), event.tenant_id
        )
        if incident is None:
            logger.error("Incident %s not found for RCA", incident_id)
            return

        alerts = await alert_repo.list(
            event.tenant_id, incident_id=incident.id, status=None, limit=1000
        )

        # Run analysis. Bind the session this service already owns so the
        # analyser can read real topology without opening a second one; analysers
        # that do not support binding (e.g. the plain rule-based engine) are
        # unaffected. Probed on the TYPE, not the instance: instance-level
        # getattr() would manufacture a mock attribute on AsyncMock analysers.
        binder = getattr(type(self.analyser), "bind_session", None)
        if callable(binder):
            binder(self.analyser, incident_repo.db)
        result = await self.analyser.analyse(incident, alerts)

        # Persist results (sync helper — flush + commit follow)
        _persist_rca_results(incident_repo, incident, result)
        await incident_repo.db.flush()
        await incident_repo.db.commit()

        # Publish RCA_COMPLETED with all needed fields, including
        # requires_approval so _on_rca_completed can decide auto_remediate.
        await self.event_bus.publish(DomainEvent(
            event_type=EventType.RCA_COMPLETED,
            tenant_id=event.tenant_id,
            correlation_id=event.correlation_id,
            payload={
                "ticket_id": ticket_id,
                "incident_id": str(incident.id),
                "root_cause_summary": result.root_cause_summary,
                "recommended_sop_id": str(result.recommended_sop_id)
                if result.recommended_sop_id is not None
                else None,
                "recommended_sop_name": result.recommended_sop_name,
                "requires_approval": result.requires_approval,
                "supporting_evidence": result.supporting_evidence,
            },
        ))

        logger.info(
            "RCA completed for incident %s — recommended_sop_id=%s",
            incident.id,
            result.recommended_sop_id,
        )
