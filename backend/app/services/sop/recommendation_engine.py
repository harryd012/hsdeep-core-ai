"""
SOP Recommendation Engine.

Orchestrator producing the sprint's mandatory per-recommendation fields:
confidence score, matching criteria breakdown, business impact, estimated
resolution time, required skills, required approvals, and rollback
procedure - for every SOP candidate matched against a real `Incident`.

Resolves real facts (root-cause category from the incident's member
alerts, vendor/platform/device_type from the linked Device/
MonitoringSource, site/business_service/region from the linked Site/
Device) then delegates scoring to the pure `SOPMatcher`. Reuses
`BusinessImpactEngine` for the business-impact field rather than
recomputing it - same "never duplicate business logic" rule as every
other sprint.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.models.alert import Alert
from app.models.device import Device
from app.models.incident import Incident
from app.models.monitoring_source import MonitoringSource
from app.models.site import Site
from app.repositories.alert_repository import AlertRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.runbook_execution_repository import RunbookExecutionRepository
from app.repositories.site_repository import SiteRepository
from app.repositories.sop_repository import SOPRepository
from app.services.priority.business_impact_engine import BusinessImpactEngine
from app.services.sop.sop_matcher import IncidentContext, SOPMatcher, SOPMatchResult


@dataclass
class SOPRecommendation:
    sop_id: uuid.UUID
    sop_number: str
    name: str
    category: str
    confidence_score: float
    matched_criteria: dict
    reasoning: dict
    business_impact_score: float
    estimated_resolution_minutes: int | None
    required_skills: list[str]
    requires_approval: bool
    rollback_procedure: str | None
    current_version_id: uuid.UUID | None


class SOPRecommendationEngine:
    def __init__(
        self,
        sop_repo: SOPRepository,
        alert_repo: AlertRepository,
        device_repo: DeviceRepository,
        site_repo: SiteRepository,
        monitoring_source_repo: MonitoringSourceRepository,
        runbook_execution_repo: RunbookExecutionRepository,
        business_impact_engine: BusinessImpactEngine,
        matcher: SOPMatcher | None = None,
    ):
        self.sop_repo = sop_repo
        self.alert_repo = alert_repo
        self.device_repo = device_repo
        self.site_repo = site_repo
        self.monitoring_source_repo = monitoring_source_repo
        self.runbook_execution_repo = runbook_execution_repo
        self.business_impact_engine = business_impact_engine
        self.matcher = matcher or SOPMatcher()

    async def recommend(
        self, incident: Incident, *, limit: int = 5, min_confidence: float = 0.0
    ) -> list[SOPRecommendation]:
        """Top `limit` SOP recommendations for `incident`, ordered by
        confidence descending. Returns an empty list (never raises, never
        fabricates a fallback) when no active/approved SOP in the tenant's
        catalog matches - callers (e.g. DecisionEngine) fall back to the
        existing `_SOP_BY_CATEGORY` string per the sprint's backward-
        compatibility requirement."""
        member_alerts = await self.alert_repo.list(incident.tenant_id, incident_id=incident.id, status=None, limit=1000)
        active_alerts = [a for a in member_alerts if a.status != "resolved"]

        context = await self.build_context(incident, active_alerts)
        impact = await self.business_impact_engine.calculate(incident.tenant_id, member_alerts)

        candidates = await self.sop_repo.list_for_matching(incident.tenant_id)

        scored: list[tuple[SOPMatchResult, float | None, int]] = []
        for sop in candidates:
            success_rate, sample_size = await self.runbook_execution_repo.success_rate_for_sop(
                incident.tenant_id, sop.id
            )
            match = self.matcher.score(sop, context, success_rate=success_rate, success_rate_sample_size=sample_size)
            if match is not None and match.confidence_score >= min_confidence:
                scored.append((match, success_rate, sample_size))

        scored.sort(key=lambda entry: entry[0].confidence_score, reverse=True)

        return [
            SOPRecommendation(
                sop_id=match.sop.id,
                sop_number=match.sop.sop_number,
                name=match.sop.name,
                category=match.sop.category,
                confidence_score=match.confidence_score,
                matched_criteria=match.matched_criteria,
                reasoning={**match.reasoning, "business_impact_score": impact.score},
                business_impact_score=impact.score,
                estimated_resolution_minutes=match.sop.estimated_resolution_minutes,
                required_skills=list(match.sop.required_skills),
                requires_approval=match.sop.requires_approval,
                rollback_procedure=match.sop.rollback_procedure,
                current_version_id=match.sop.current_version_id,
            )
            for match, _, _ in scored[:limit]
        ]

    async def build_context(self, incident: Incident, active_alerts: list[Alert]) -> IncidentContext:
        """Public so callers outside SOP matching (e.g. WorkflowSelector's
        trigger evaluation in alert_service.py) can build and reuse the
        exact same real-fact resolution this class already does, rather
        than duplicating device/site/monitoring_source lookups."""
        root_cause_alerts = [a for a in active_alerts if a.is_root_cause_candidate]
        reference_alert = root_cause_alerts[0] if root_cause_alerts else (active_alerts[0] if active_alerts else None)

        category = reference_alert.category if reference_alert is not None else None
        vendor = reference_alert.vendor if reference_alert is not None else None
        region = reference_alert.region if reference_alert is not None else None

        platform: str | None = None
        device_type: str | None = None
        business_service_id = None

        device_id = incident.device_id or (reference_alert.device_id if reference_alert is not None else None)
        if device_id is not None:
            device = await self.device_repo.get(device_id, incident.tenant_id)
            if device is not None:
                device_type = device.device_type
                vendor = vendor or device.vendor
                business_service_id = device.business_service_id
                if device.monitoring_source_id is not None:
                    source = await self.monitoring_source_repo.get(device.monitoring_source_id, incident.tenant_id)
                    if source is not None:
                        platform = source.source_type

        if platform is None and reference_alert is not None and reference_alert.monitoring_source_id is not None:
            source = await self.monitoring_source_repo.get(reference_alert.monitoring_source_id, incident.tenant_id)
            if source is not None:
                platform = source.source_type

        return IncidentContext(
            category=category,
            vendor=vendor,
            platform=platform,
            device_type=device_type,
            site_id=incident.site_id,
            business_service_id=business_service_id,
            region=region,
            priority=incident.priority,
        )
