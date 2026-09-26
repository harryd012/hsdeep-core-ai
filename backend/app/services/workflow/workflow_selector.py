"""
Workflow Selector - Enterprise Workflow Automation Engine sprint.

Scores active, approved Workflows registered for a given `trigger_type`
against real Incident/Alert context, same hard-exclude/soft-credit
scoring pattern as `SOPMatcher` (see services/sop/sop_matcher.py) applied
to `Workflow.trigger_filter` instead of `SOP`'s matching columns.
Deliberately reuses the exact same criteria-credit philosophy rather than
introducing a second scoring convention in the codebase.

`trigger_filter` keys checked (all optional on the Workflow row):
    category, min_priority, vendor, platform, device_type, site_id,
    business_service_id
An unset key means "this workflow applies regardless of that dimension"
(broad trigger) - a SET key that contradicts the real incident context is
a hard exclusion, exactly mirroring SOPMatcher's rule for SOP.vendor/
platform/etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.workflow import Workflow
from app.services.sop.sop_matcher import IncidentContext

_PRIORITY_RANK = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}


@dataclass
class WorkflowMatchResult:
    workflow: Workflow
    confidence_score: float
    matched_criteria: dict
    reasoning: dict = field(default_factory=dict)


class WorkflowSelector:
    def score(self, workflow: Workflow, context: IncidentContext) -> WorkflowMatchResult | None:
        """Reuses the same IncidentContext dataclass SOPMatcher already
        builds (services/sop/sop_matcher.py) - WorkflowSelector never
        re-resolves real Device/Site/MonitoringSource facts itself; the
        caller (see workflow_service.py's evaluate_triggers()) passes the
        same context it already built for SOP matching, so no business
        logic is duplicated."""
        trigger_filter = workflow.trigger_filter or {}
        if not trigger_filter:
            return WorkflowMatchResult(
                workflow=workflow, confidence_score=100.0, matched_criteria={}, reasoning={"trigger_filter": "unset (fires unconditionally for this trigger_type)"}
            )

        matched: dict[str, bool | None] = {}
        reasoning: dict[str, str] = {}
        total_weight = 0.0
        earned = 0.0

        checks = [
            ("category", trigger_filter.get("category"), context.category),
            ("vendor", trigger_filter.get("vendor"), context.vendor),
            ("platform", trigger_filter.get("platform"), context.platform),
            ("device_type", trigger_filter.get("device_type"), context.device_type),
            ("site_id", trigger_filter.get("site_id"), str(context.site_id) if context.site_id else None),
            (
                "business_service_id",
                trigger_filter.get("business_service_id"),
                str(context.business_service_id) if context.business_service_id else None,
            ),
        ]
        for name, expected, actual in checks:
            if expected is None:
                continue
            total_weight += 1.0
            if actual is None:
                earned += 0.25
                matched[name] = None
                reasoning[name] = f"incident's {name} is unknown; workflow requires {expected!r}"
            elif str(actual) == str(expected):
                earned += 1.0
                matched[name] = True
                reasoning[name] = f"matched ({expected!r})"
            else:
                matched[name] = False
                reasoning[name] = f"mismatch: workflow requires {expected!r}, incident has {actual!r}"
                return None  # hard exclusion

        min_priority = trigger_filter.get("min_priority")
        if min_priority is not None:
            total_weight += 1.0
            incident_rank = _PRIORITY_RANK.get(context.priority) if context.priority else None
            required_rank = _PRIORITY_RANK.get(min_priority)
            if incident_rank is None or required_rank is None:
                earned += 0.25
                matched["min_priority"] = None
                reasoning["min_priority"] = "priority unknown or unrecognized"
            elif incident_rank <= required_rank:
                earned += 1.0
                matched["min_priority"] = True
                reasoning["min_priority"] = f"incident priority {context.priority} meets minimum {min_priority}"
            else:
                matched["min_priority"] = False
                reasoning["min_priority"] = f"incident priority {context.priority} is below minimum {min_priority}"
                return None  # hard exclusion

        confidence = round((earned / total_weight) * 100.0, 1) if total_weight > 0 else 100.0
        return WorkflowMatchResult(workflow=workflow, confidence_score=confidence, matched_criteria=matched, reasoning=reasoning)
