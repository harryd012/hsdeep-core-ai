"""
AI Decision Engine.

Generates the sprint's "Recommended X" fields from the already-computed
priority/risk scores plus a few real incident facts (category, affected
device count). Every rule is a plain if/elif lookup table - no external
LLM call, per the sprint brief.

Substitution point for a future model: `DecisionEngine.recommend()` is the
ONLY place these five fields are produced, and its signature (priority,
risk scores, incident facts -> recommendations) is generic enough that a
future LLM-backed implementation could satisfy the exact same call site
in `PriorityEngine.recompute()` without any caller changing - this is the
sprint's "future AI/LLM can replace the rule engine" requirement, achieved
by keeping this class's public interface narrow and swappable rather than
scattering rule logic across PriorityEngine itself.
"""
from __future__ import annotations

from dataclasses import dataclass

_RESPONSE_TIME_MINUTES = {"P1": 15, "P2": 60, "P3": 240, "P4": 1440}
_ESCALATION_LEVEL = {"P1": "L3 - Senior Engineer + Manager", "P2": "L2 - On-call Engineer", "P3": "L1 - Support Queue", "P4": "L1 - Backlog"}
_TEAM_BY_CATEGORY = {
    "availability": "Network Operations",
    "connectivity": "Network Operations",
    "performance": "Infrastructure Engineering",
    "capacity": "Infrastructure Engineering",
    "configuration": "Systems Engineering",
    "security": "Security Operations",
    "other": "Tier 1 Support",
}
_SOP_BY_CATEGORY = {
    "availability": "Device/Service Outage Response",
    "connectivity": "Network Path Degradation Response",
    "performance": "Performance Degradation Investigation",
    "capacity": "Capacity Threshold Response",
    "configuration": "Configuration Drift Investigation",
    "security": "Security Incident Response",
    "other": "General Incident Triage",
}


@dataclass
class DecisionResult:
    escalation_level: str
    recommended_team: str
    recommended_response_time_minutes: int
    recommended_next_action: str
    recommended_sop: str


class DecisionEngine:
    def recommend(
        self,
        *,
        priority: str,
        root_cause_category: str | None,
        affected_device_count: int,
        is_new_incident: bool,
        root_cause_identified: bool,
    ) -> DecisionResult:
        category = root_cause_category or "other"
        team = _TEAM_BY_CATEGORY.get(category, _TEAM_BY_CATEGORY["other"])
        sop = _SOP_BY_CATEGORY.get(category, _SOP_BY_CATEGORY["other"])

        next_action = self._next_action(
            priority=priority,
            root_cause_identified=root_cause_identified,
            affected_device_count=affected_device_count,
            is_new_incident=is_new_incident,
        )

        return DecisionResult(
            escalation_level=_ESCALATION_LEVEL[priority],
            recommended_team=team,
            recommended_response_time_minutes=_RESPONSE_TIME_MINUTES[priority],
            recommended_next_action=next_action,
            recommended_sop=sop,
        )

    @staticmethod
    def _next_action(
        *, priority: str, root_cause_identified: bool, affected_device_count: int, is_new_incident: bool
    ) -> str:
        if priority == "P1" and not root_cause_identified:
            return (
                "Immediately investigate root cause - no confident root cause identified yet for a "
                "P1 incident. Check device/interface health directly."
            )
        if priority == "P1":
            return "Engage on-call response immediately; root cause identified - begin remediation."
        if is_new_incident and affected_device_count > 1:
            return f"Review the {affected_device_count} affected devices to confirm shared root cause before escalating."
        if not root_cause_identified:
            return "Monitor for a confident root cause candidate; escalate if severity increases."
        return "Proceed per assigned SOP; no immediate escalation required."
