"""
Priority Engine (orchestrator).

`PriorityEngine.recompute(incident)` is the single entry point: gathers the
incident's active member alerts, runs BusinessImpactEngine -> RiskEngine ->
confidence scoring -> priority-level mapping -> DecisionEngine, and
persists everything onto the Incident row via IncidentRepository. Mirrors
CorrelationEngine's shape exactly (one public `correlate()`/`recompute()`
method, private helpers, repository-only persistence) so this reads as an
extension of the existing architecture, not a new pattern.

Priority level mapping (P1-P4) - the sprint's core "Priority Calculation"
requirement - combines every named input from the brief:
  Alert Severity        -> folded into technical_risk_score (RiskEngine)
  Incident Size          -> folded into operational_risk_score
  Business Service        -> folded into business_risk_score (via BusinessImpactEngine)
  Site Importance         -> folded into business_risk_score
  Device Type              -> device criticality, folded into business_risk_score
  Alert Frequency          -> folded into operational_risk_score (occurrence_count)
  Incident Duration         -> folded into operational_risk_score
  Repeated Failures          -> folded into operational_risk_score
  Root Cause Confidence       -> folded into technical_risk_score + incident_confidence_score
  Maintenance Window            -> `_maintenance_window_factor()` below, applied as a
                                  direct dampener on ai_risk_score before mapping to P1-P4
  Business Hours                 -> `_business_hours_factor()` below, same treatment
  Manual Overrides                -> handled entirely in IncidentRepository.set_priority_scores()/
                                  set_manual_override() - PriorityEngine always computes the
                                  "would-be" priority but the repository is what actually
                                  respects the pin.

ai_risk_score (0-100) is what actually gets thresholded into P1-P4, AFTER
the maintenance-window/business-hours dampeners are applied - those two
factors don't change the underlying risk math, they change how urgently a
human needs to respond to it right now, which is exactly what the P1-P4
levels represent ("Immediate Response" vs "Normal Queue").
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.alert import Alert
from app.models.incident import Incident
from app.repositories.alert_repository import AlertRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.maintenance_window_repository import MaintenanceWindowRepository
from app.services.priority.business_impact_engine import BusinessImpactEngine
from app.services.priority.confidence_scorer import incident_confidence
from app.services.priority.decision_engine import DecisionEngine
from app.services.priority.risk_engine import RiskEngine

# ai_risk_score thresholds mapping onto P1-P4. Named constants (not magic
# numbers inline) for explainability - PriorityEngine's reasoning payload
# quotes these directly.
_PRIORITY_THRESHOLDS = (
    ("P1", 75.0),
    ("P2", 50.0),
    ("P3", 25.0),
)
_DEFAULT_PRIORITY = "P4"

# Maintenance window / off-hours dampeners applied to ai_risk_score before
# thresholding - a real incident under planned maintenance is still
# tracked at its true risk internally (nothing here mutates the stored
# risk scores) but is deprioritized for HUMAN response ordering, which is
# what the P1-P4 level represents.
_MAINTENANCE_WINDOW_DAMPENER = 0.5  # halves effective urgency during a window
_OFF_HOURS_DAMPENER = 0.85          # small dampener outside business hours (fewer available responders != lower risk, but queue ordering still shifts slightly)

# Business hours (UTC), deterministic and documented simplification - no
# tenant/site timezone field exists yet (see design note in
# models/site.py history). Flagged here rather than silently guessing a
# timezone.
_BUSINESS_HOURS_START_UTC = 9
_BUSINESS_HOURS_END_UTC = 17


@dataclass
class PriorityResult:
    priority: str
    operational_risk_score: float
    business_risk_score: float
    technical_risk_score: float
    ai_risk_score: float
    incident_confidence_score: float
    business_impact_score: float
    reasoning: dict


class PriorityEngine:
    def __init__(
        self,
        alert_repo: AlertRepository,
        incident_repo: IncidentRepository,
        maintenance_window_repo: MaintenanceWindowRepository,
        business_impact_engine: BusinessImpactEngine,
        risk_engine: RiskEngine,
        decision_engine: DecisionEngine,
    ):
        self.alert_repo = alert_repo
        self.incident_repo = incident_repo
        self.maintenance_window_repo = maintenance_window_repo
        self.business_impact_engine = business_impact_engine
        self.risk_engine = risk_engine
        self.decision_engine = decision_engine

    async def recompute(self, incident: Incident) -> Incident:
        """Recomputes and persists every priority/risk/confidence field on
        `incident`. Safe to call repeatedly (idempotent given the same
        underlying data) - same convention as CorrelationEngine.correlate().
        """
        now = datetime.now(timezone.utc)

        member_alerts = await self.alert_repo.list(
            incident.tenant_id, incident_id=incident.id, status=None, limit=1000
        )
        active_alerts = [a for a in member_alerts if a.status != "resolved"]

        impact = await self.business_impact_engine.calculate(incident.tenant_id, member_alerts)
        risk = self.risk_engine.calculate(incident, member_alerts, impact.score, now=now)

        root_cause_alerts = [a for a in active_alerts if a.is_root_cause_candidate]
        root_cause_identified = bool(root_cause_alerts)
        root_cause_category = root_cause_alerts[0].category if root_cause_alerts else None

        inputs_fully_classified = impact.affected_device_count > 0 and bool(impact.affected_business_services)
        confidence_score, confidence_reasoning = incident_confidence(
            active_alerts,
            root_cause_identified=root_cause_identified,
            inputs_fully_classified=inputs_fully_classified,
        )

        maintenance_factor, maintenance_reasoning = await self._maintenance_window_factor(incident, now)
        hours_factor, hours_reasoning = self._business_hours_factor(now)

        effective_ai_risk = risk.ai_risk_score * maintenance_factor * hours_factor
        priority = self._priority_for_score(effective_ai_risk)

        decision = self.decision_engine.recommend(
            priority=priority,
            root_cause_category=root_cause_category,
            affected_device_count=impact.affected_device_count,
            is_new_incident=incident.alert_count <= 1,
            root_cause_identified=root_cause_identified,
        )

        reasoning = {
            "priority": {
                "value": priority,
                "effective_ai_risk_score": round(effective_ai_risk, 1),
                "raw_ai_risk_score": risk.ai_risk_score,
                "thresholds": dict(_PRIORITY_THRESHOLDS),
                "manual_override_active": incident.manual_priority_override is not None,
            },
            "business_impact": impact.reasoning,
            "risk": risk.reasoning,
            "incident_confidence": confidence_reasoning,
            "maintenance_window": maintenance_reasoning,
            "business_hours": hours_reasoning,
            "root_cause": {
                "identified": root_cause_identified,
                "category": root_cause_category,
                "candidate_alert_ids": [str(a.id) for a in root_cause_alerts],
            },
        }

        return await self.incident_repo.set_priority_scores(
            incident,
            priority=priority,
            operational_risk_score=risk.operational_risk_score,
            business_risk_score=risk.business_risk_score,
            technical_risk_score=risk.technical_risk_score,
            ai_risk_score=risk.ai_risk_score,
            incident_confidence_score=confidence_score,
            business_impact_score=impact.score,
            escalation_level=decision.escalation_level,
            recommended_team=decision.recommended_team,
            recommended_response_time_minutes=decision.recommended_response_time_minutes,
            recommended_next_action=decision.recommended_next_action,
            recommended_sop=decision.recommended_sop,
            priority_reasoning=reasoning,
            calculated_at=now,
        )

    @staticmethod
    def _priority_for_score(effective_ai_risk: float) -> str:
        for priority, threshold in _PRIORITY_THRESHOLDS:
            if effective_ai_risk >= threshold:
                return priority
        return _DEFAULT_PRIORITY

    async def _maintenance_window_factor(self, incident: Incident, now: datetime) -> tuple[float, dict]:
        windows = await self.maintenance_window_repo.list_active_for_scope(
            incident.tenant_id, site_id=incident.site_id, device_id=incident.device_id, at=now
        )
        if windows:
            return _MAINTENANCE_WINDOW_DAMPENER, {
                "active": True,
                "window_count": len(windows),
                "dampener_applied": _MAINTENANCE_WINDOW_DAMPENER,
            }
        return 1.0, {"active": False, "dampener_applied": 1.0}

    @staticmethod
    def _business_hours_factor(now: datetime) -> tuple[float, dict]:
        """UTC-based, deterministic. Documented simplification: no tenant/
        site timezone field exists yet in this schema - see this module's
        docstring. Weekends are also treated as off-hours."""
        is_weekend = now.weekday() >= 5
        is_within_hours = _BUSINESS_HOURS_START_UTC <= now.hour < _BUSINESS_HOURS_END_UTC
        within_business_hours = is_within_hours and not is_weekend
        factor = 1.0 if within_business_hours else _OFF_HOURS_DAMPENER
        return factor, {
            "within_business_hours_utc": within_business_hours,
            "hour_utc": now.hour,
            "is_weekend": is_weekend,
            "dampener_applied": factor,
        }
