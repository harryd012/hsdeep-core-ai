"""
Risk Engine.

Computes four 0-100 scores for an incident - Operational, Business,
Technical, and an overall AI Risk Score (a weighted blend of the other
three) - each with a transparent, inspectable reasoning breakdown per the
sprint's explainability requirement. Every input is a real stored value;
nothing here is randomized or simulated.

Score definitions (deliberately narrow and explainable, not a black box):

  Operational Risk  - how disruptive this incident is to day-to-day
                       operations right now: incident size (alert_count),
                       duration open, repeated-failure frequency
                       (max member alert occurrence_count).
  Business Risk      - how much this incident threatens the business:
                       BusinessImpactEngine's score, directly.
  Technical Risk     - how technically severe/uncertain the underlying
                       condition is: max member severity, inverse of
                       root-cause confidence (a confident root cause is
                       LOWER technical risk - it's understood, not a
                       mystery), whether any evidence exists at all.
  AI Risk Score       - weighted blend: 35% operational + 35% business +
                       30% technical. Weights are named constants for
                       the same reasoning-transparency requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.alert import Alert
from app.models.incident import Incident
from app.services.priority.criticality_weights import severity_weight

_AI_RISK_WEIGHT_OPERATIONAL = 0.35
_AI_RISK_WEIGHT_BUSINESS = 0.35
_AI_RISK_WEIGHT_TECHNICAL = 0.30

# Incident-size and duration are diminishing-returns capped, same
# rationale as BusinessImpactEngine's device/user count caps.
_ALERT_COUNT_SATURATION = 20
_DURATION_SATURATION_HOURS = 4.0
_OCCURRENCE_SATURATION = 10


@dataclass
class RiskScores:
    operational_risk_score: float
    business_risk_score: float
    technical_risk_score: float
    ai_risk_score: float
    reasoning: dict = field(default_factory=dict)


class RiskEngine:
    def calculate(
        self, incident: Incident, member_alerts: list[Alert], business_impact_score: float, now: datetime | None = None
    ) -> RiskScores:
        now = now or datetime.now(timezone.utc)
        active_alerts = [a for a in member_alerts if a.status != "resolved"]

        operational_score, operational_reasoning = self._operational_risk(incident, active_alerts, now)
        business_score, business_reasoning = business_impact_score, {"business_impact_score": business_impact_score}
        technical_score, technical_reasoning = self._technical_risk(active_alerts)

        ai_score = (
            operational_score * _AI_RISK_WEIGHT_OPERATIONAL
            + business_score * _AI_RISK_WEIGHT_BUSINESS
            + technical_score * _AI_RISK_WEIGHT_TECHNICAL
        )

        reasoning = {
            "operational_risk": operational_reasoning,
            "business_risk": business_reasoning,
            "technical_risk": technical_reasoning,
            "ai_risk_weights": {
                "operational": _AI_RISK_WEIGHT_OPERATIONAL,
                "business": _AI_RISK_WEIGHT_BUSINESS,
                "technical": _AI_RISK_WEIGHT_TECHNICAL,
            },
        }

        return RiskScores(
            operational_risk_score=round(operational_score, 1),
            business_risk_score=round(business_score, 1),
            technical_risk_score=round(technical_score, 1),
            ai_risk_score=round(ai_score, 1),
            reasoning=reasoning,
        )

    @staticmethod
    def _operational_risk(incident: Incident, active_alerts: list[Alert], now: datetime) -> tuple[float, dict]:
        size_component = min(len(active_alerts) / _ALERT_COUNT_SATURATION, 1.0) * 100.0

        # Clamped to >= 0: `incident.opened_at` is sourced from a vendor's
        # own reported timestamp (e.g. PRTG's message datetime), which can
        # occasionally read as slightly "in the future" relative to this
        # process's UTC clock (clock skew or a vendor timestamp parsed
        # without full timezone fidelity - see collectors/prtg.py's
        # _parse_prtg_datetime). A negative duration_hours must never be
        # allowed to silently DECREASE operational_risk_score below what
        # duration=0 would already produce.
        duration_hours = max((now - incident.opened_at).total_seconds() / 3600.0, 0.0)
        duration_component = min(duration_hours / _DURATION_SATURATION_HOURS, 1.0) * 100.0

        max_occurrences = max((a.occurrence_count for a in active_alerts), default=0)
        frequency_component = min(max_occurrences / _OCCURRENCE_SATURATION, 1.0) * 100.0

        # Equal-weighted average of the three sub-factors.
        score = (size_component + duration_component + frequency_component) / 3.0

        return score, {
            "active_alert_count": len(active_alerts),
            "size_component": round(size_component, 1),
            "duration_hours": round(duration_hours, 2),
            "duration_component": round(duration_component, 1),
            "max_occurrence_count": max_occurrences,
            "frequency_component": round(frequency_component, 1),
        }

    @staticmethod
    def _technical_risk(active_alerts: list[Alert]) -> tuple[float, dict]:
        if not active_alerts:
            return 0.0, {"reason": "no active member alerts"}

        max_severity_weight = max(severity_weight(a.severity) for a in active_alerts)

        root_cause_alerts = [a for a in active_alerts if a.is_root_cause_candidate]
        # A confident, identified root cause LOWERS technical risk (the
        # condition is understood); no root cause identified yet keeps
        # technical risk elevated (uncertainty itself is a technical risk).
        root_cause_confidence = (
            max((a.confidence_score or 0.0) for a in root_cause_alerts) if root_cause_alerts else 0.0
        )
        uncertainty_component = 100.0 - root_cause_confidence

        score = (max_severity_weight * 0.6) + (uncertainty_component * 0.4)

        return score, {
            "max_severity_weight": round(max_severity_weight, 1),
            "root_cause_identified": bool(root_cause_alerts),
            "root_cause_confidence": round(root_cause_confidence, 1),
            "uncertainty_component": round(uncertainty_component, 1),
        }
