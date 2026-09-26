"""
AI Priority Engine API.

NOTE(auth): same interim pattern as every other router in this codebase -
tenant_id is an explicit query parameter because there is no
authenticated-principal dependency yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.alert_repository import AlertRepository
from app.repositories.business_service_repository import BusinessServiceRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.maintenance_window_repository import MaintenanceWindowRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.incident import IncidentOut
from app.schemas.priority import (
    BusinessImpactSummary,
    ManualOverrideRequest,
    Priority,
    PriorityDistribution,
    PriorityOut,
    PrioritizedIncidentOut,
    RiskHeatmapCell,
    RiskSummary,
)
from app.services.priority.business_impact_engine import BusinessImpactEngine
from app.services.priority.decision_engine import DecisionEngine
from app.services.priority.priority_engine import PriorityEngine
from app.services.priority.risk_engine import RiskEngine
from app.services.priority_service import IncidentNotFoundForPriority, PriorityService

router = APIRouter(tags=["priorities"])



# Suspended/canceled/incomplete tenants cannot WRITE (402);
# reads stay open and past_due keeps its grace period.
_active_sub_guard = Depends(require_active_subscription)
def get_priority_service(db: AsyncSession = Depends(get_db)) -> PriorityService:
    incident_repo = IncidentRepository(db)
    alert_repo = AlertRepository(db)
    priority_engine = PriorityEngine(
        alert_repo=alert_repo,
        incident_repo=incident_repo,
        maintenance_window_repo=MaintenanceWindowRepository(db),
        business_impact_engine=BusinessImpactEngine(
            device_repo=DeviceRepository(db),
            site_repo=SiteRepository(db),
            business_service_repo=BusinessServiceRepository(db),
        ),
        risk_engine=RiskEngine(),
        decision_engine=DecisionEngine(),
    )
    return PriorityService(incident_repo=incident_repo, priority_engine=priority_engine)


def _to_prioritized_out(incident) -> PrioritizedIncidentOut:
    base = IncidentOut.model_validate(incident, from_attributes=True)
    return PrioritizedIncidentOut(
        **base.model_dump(),
        priority=incident.priority,
        operational_risk_score=incident.operational_risk_score,
        business_risk_score=incident.business_risk_score,
        technical_risk_score=incident.technical_risk_score,
        ai_risk_score=incident.ai_risk_score,
        incident_confidence_score=incident.incident_confidence_score,
        business_impact_score=incident.business_impact_score,
        escalation_level=incident.escalation_level,
        recommended_team=incident.recommended_team,
        recommended_response_time_minutes=incident.recommended_response_time_minutes,
        recommended_next_action=incident.recommended_next_action,
        recommended_sop=incident.recommended_sop,
    )


def _to_heatmap_cell(incident) -> RiskHeatmapCell:
    return RiskHeatmapCell(
        incident_id=incident.id,
        title=incident.title,
        priority=incident.priority,
        business_risk_score=incident.business_risk_score,
        operational_risk_score=incident.operational_risk_score,
        technical_risk_score=incident.technical_risk_score,
        ai_risk_score=incident.ai_risk_score,
    )


@router.get("/priorities", response_model=list[PrioritizedIncidentOut])
async def list_priorities(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    status: str | None = Query(default="open"),
    priority: Priority | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: PriorityService = Depends(get_priority_service),
):
    incidents = await service.list_prioritized(
        tenant_id, status=status, priority=priority, limit=limit, offset=offset
    )
    return [_to_prioritized_out(i) for i in incidents]


@router.get("/priorities/{incident_id}", response_model=PriorityOut)
async def get_priority(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    recompute: bool = Query(default=False, description="Force a fresh recompute before returning."),
    service: PriorityService = Depends(get_priority_service),
):
    try:
        incident = await (
            service.recompute(incident_id, tenant_id) if recompute else service.get(incident_id, tenant_id)
        )
    except IncidentNotFoundForPriority as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PriorityOut(
        incident_id=incident.id,
        priority=incident.priority,
        operational_risk_score=incident.operational_risk_score,
        business_risk_score=incident.business_risk_score,
        technical_risk_score=incident.technical_risk_score,
        ai_risk_score=incident.ai_risk_score,
        incident_confidence_score=incident.incident_confidence_score,
        business_impact_score=incident.business_impact_score,
        escalation_level=incident.escalation_level,
        recommended_team=incident.recommended_team,
        recommended_response_time_minutes=incident.recommended_response_time_minutes,
        recommended_next_action=incident.recommended_next_action,
        recommended_sop=incident.recommended_sop,
        priority_reasoning=incident.priority_reasoning,
        priority_calculated_at=incident.priority_calculated_at,
        manual_priority_override=incident.manual_priority_override,
        priority_overridden_by=incident.priority_overridden_by,
        priority_overridden_at=incident.priority_overridden_at,
    )


@router.post("/priorities/{incident_id}/override", dependencies=[_active_sub_guard], response_model=PriorityOut)
async def set_manual_override(
    incident_id: uuid.UUID,
    payload: ManualOverrideRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: PriorityService = Depends(get_priority_service),
):
    try:
        incident = await service.set_manual_override(
            incident_id, tenant_id, priority=payload.priority, overridden_by=payload.overridden_by
        )
    except IncidentNotFoundForPriority as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()

    return PriorityOut(
        incident_id=incident.id,
        priority=incident.priority,
        operational_risk_score=incident.operational_risk_score,
        business_risk_score=incident.business_risk_score,
        technical_risk_score=incident.technical_risk_score,
        ai_risk_score=incident.ai_risk_score,
        incident_confidence_score=incident.incident_confidence_score,
        business_impact_score=incident.business_impact_score,
        escalation_level=incident.escalation_level,
        recommended_team=incident.recommended_team,
        recommended_response_time_minutes=incident.recommended_response_time_minutes,
        recommended_next_action=incident.recommended_next_action,
        recommended_sop=incident.recommended_sop,
        priority_reasoning=incident.priority_reasoning,
        priority_calculated_at=incident.priority_calculated_at,
        manual_priority_override=incident.manual_priority_override,
        priority_overridden_by=incident.priority_overridden_by,
        priority_overridden_at=incident.priority_overridden_at,
    )


@router.get("/risk", response_model=RiskSummary)
async def get_risk_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: PriorityService = Depends(get_priority_service),
):
    distribution = await service.priority_distribution(tenant_id)
    open_incidents = await service.list_prioritized(tenant_id, status="open", limit=1000)

    scored = [i for i in open_incidents if i.ai_risk_score is not None]
    avg_risk = sum(i.ai_risk_score for i in scored) / len(scored) if scored else 0.0

    ranked = sorted(scored, key=lambda i: i.ai_risk_score, reverse=True)

    return RiskSummary(
        generated_at=datetime.now(timezone.utc),
        priority_distribution=PriorityDistribution(**distribution),
        avg_ai_risk_score=round(avg_risk, 1),
        highest_risk_incidents=[_to_heatmap_cell(i) for i in ranked[:10]],
        heatmap=[_to_heatmap_cell(i) for i in scored],
    )


@router.get("/business-impact", response_model=BusinessImpactSummary)
async def get_business_impact_summary(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: PriorityService = Depends(get_priority_service),
):
    open_incidents = await service.list_prioritized(tenant_id, status="open", limit=1000)
    scored = [i for i in open_incidents if i.business_impact_score is not None]

    ranked = sorted(scored, key=lambda i: i.business_impact_score, reverse=True)
    critical_count = sum(1 for i in open_incidents if i.priority == "P1")

    # total_estimated_affected_users/total_affected_devices are best read
    # from priority_reasoning (the exact numbers PriorityEngine already
    # computed and stored, per the explainability requirement) rather than
    # re-querying BusinessImpactEngine here - avoids a second, potentially
    # inconsistent computation of the same numbers.
    total_users = sum(
        (i.priority_reasoning or {}).get("business_impact", {}).get("estimated_affected_users", 0)
        for i in open_incidents
    )
    total_devices = sum(
        (i.priority_reasoning or {}).get("business_impact", {}).get("affected_device_count", 0)
        for i in open_incidents
    )

    return BusinessImpactSummary(
        generated_at=datetime.now(timezone.utc),
        total_estimated_affected_users=total_users,
        total_affected_devices=total_devices,
        critical_incident_count=critical_count,
        top_incidents_by_impact=[_to_heatmap_cell(i) for i in ranked[:10]],
    )
