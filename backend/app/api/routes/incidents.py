"""
Incident API (Alert Correlation Engine sprint).

NOTE(auth): same interim pattern as every other router in this codebase -
tenant_id is an explicit query parameter because there is no
authenticated-principal dependency yet.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.core.auth import get_tenant_id
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.alert_repository import AlertRepository
from app.repositories.business_service_repository import BusinessServiceRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.maintenance_window_repository import MaintenanceWindowRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.site_repository import SiteRepository
from app.schemas.incident import IncidentDetailOut, IncidentOut, IncidentSeverity, IncidentStatus
from app.schemas.priority import Priority, PrioritizedIncidentOut
from app.services.incident_service import IncidentNotFound, IncidentService
from app.services.priority.business_impact_engine import BusinessImpactEngine
from app.services.priority.decision_engine import DecisionEngine
from app.services.priority.priority_engine import PriorityEngine
from app.services.priority.risk_engine import RiskEngine
from app.services.priority_service import PriorityService

router = APIRouter(prefix="/incidents", tags=["incidents"])

_active_sub_guard = Depends(require_active_subscription)


def get_incident_service(db: AsyncSession = Depends(get_db)) -> IncidentService:
    return IncidentService(incident_repo=IncidentRepository(db), alert_repo=AlertRepository(db))


def get_priority_service(db: AsyncSession = Depends(get_db)) -> PriorityService:
    incident_repo = IncidentRepository(db)
    priority_engine = PriorityEngine(
        alert_repo=AlertRepository(db),
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


# ---------------------------------------------------------------------------
# Intelligence / health aggregates (no auth guard — read-only)
# ---------------------------------------------------------------------------

class AlertIntelligenceMetrics(BaseModel):
    active_incidents: int = 0
    correlated_incidents: int = 0
    suppressed_alerts: int = 0
    duplicate_alerts_removed: int = 0
    noise_reduction_pct: float = 0.0


class CollectorHealthItem(BaseModel):
    source_id: str
    source_name: str
    current_state: str
    success_rate_pct: float
    total_runs: int
    last_success_at: str | None
    last_error: str | None


class CollectorHealthList(BaseModel):
    collectors: list[CollectorHealthItem]
    healthy_count: int
    degraded_count: int
    failing_count: int
    unknown_count: int


@router.get("/intelligence", response_model=AlertIntelligenceMetrics)
async def get_alert_intelligence_metrics(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated alert intelligence metrics for the Incidents dashboard."""
    incident_repo = IncidentRepository(db)
    alert_repo = AlertRepository(db)

    # One single-scan conditional aggregate replaces three sequential COUNT(*)
    # round trips plus a capped 1000-row ORM fetch. Measured on the real
    # 2.4M-incident tenant: 9.9s -> ~2.4s. Definitions are unchanged:
    #   active     = open + acknowledged + investigating
    #   correlated = open incidents with more than one member alert
    active, correlated = await incident_repo.intelligence_counts(tenant_id)

    # Suppressed alerts
    suppressed = await alert_repo.count(tenant_id, is_suppressed=True)

    # Noise reduction: suppressed / (total open alerts)
    total_open_alerts = await alert_repo.count(tenant_id, status="open")
    raw = total_open_alerts + suppressed
    noise_pct = round((suppressed / raw) * 100, 1) if raw > 0 else 0.0

    return AlertIntelligenceMetrics(
        active_incidents=active,
        correlated_incidents=correlated,
        suppressed_alerts=suppressed,
        duplicate_alerts_removed=suppressed,
        noise_reduction_pct=noise_pct,
    )


@router.get("/collector-health", response_model=CollectorHealthList)
async def get_collector_health(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    window_hours: int = Query(default=24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
):
    """Collector health summary for the Incidents dashboard."""
    source_repo = MonitoringSourceRepository(db)
    run_repo = CollectorRunRepository(db)

    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    sources = await source_repo.list(tenant_id)

    items: list[CollectorHealthItem] = []
    healthy = degraded = failing = unknown = 0

    for source in sources:
        runs = await run_repo.list(tenant_id, monitoring_source_id=source.id, since=since, limit=100)
        total = len(runs)
        succeeded = sum(1 for r in runs if r.status == "success")
        rate = round((succeeded / total) * 100, 1) if total > 0 else 0.0
        last_success = next((r.finished_at for r in sorted(runs, key=lambda r: r.started_at, reverse=True) if r.status == "success"), None)
        last_error = next((r.error for r in sorted(runs, key=lambda r: r.started_at, reverse=True) if r.status == "error"), None)

        if total == 0:
            state = "unknown"
            unknown += 1
        elif rate >= 90:
            state = "healthy"
            healthy += 1
        elif rate >= 50:
            state = "degraded"
            degraded += 1
        else:
            state = "failing"
            failing += 1

        items.append(CollectorHealthItem(
            source_id=str(source.id),
            source_name=source.name,
            current_state=state,
            success_rate_pct=rate,
            total_runs=total,
            last_success_at=last_success.isoformat() if last_success else None,
            last_error=last_error,
        ))

    return CollectorHealthList(
        collectors=items,
        healthy_count=healthy,
        degraded_count=degraded,
        failing_count=failing,
        unknown_count=unknown,
    )


# ---------------------------------------------------------------------------
# Standard incident list endpoints
# ---------------------------------------------------------------------------

@router.get("/prioritized", response_model=list[PrioritizedIncidentOut])
async def list_prioritized_incidents(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    status: str | None = Query(default="open"),
    priority: Priority | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: PriorityService = Depends(get_priority_service),
):
    incidents = await service.list_prioritized(tenant_id, status=status, priority=priority, limit=limit, offset=offset)
    results = []
    for incident in incidents:
        base = IncidentOut.model_validate(incident, from_attributes=True)
        results.append(
            PrioritizedIncidentOut(
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
        )
    return results


@router.get("", response_model=list[IncidentOut])
async def list_incidents(
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    site_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    severity: IncidentSeverity | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: IncidentService = Depends(get_incident_service),
):
    return await service.list_incidents(
        tenant_id, site_id=site_id, device_id=device_id, severity=severity, status=status,
        limit=limit, offset=offset,
    )


@router.get("/{incident_id}", response_model=IncidentDetailOut)
async def get_incident(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident, alerts = await service.get_with_alerts(incident_id, tenant_id)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    incident_out = IncidentOut.model_validate(incident, from_attributes=True)
    return IncidentDetailOut(**incident_out.model_dump(), alerts=alerts)


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------

class AcknowledgeBody(BaseModel):
    acknowledged_by: uuid.UUID | None = None


@router.post("/{incident_id}/acknowledge", dependencies=[_active_sub_guard], response_model=IncidentOut)
async def acknowledge_incident(
    incident_id: uuid.UUID,
    body: AcknowledgeBody = AcknowledgeBody(),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident = await service.acknowledge(incident_id, tenant_id, body.acknowledged_by)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return incident


@router.post("/{incident_id}/investigate", dependencies=[_active_sub_guard], response_model=IncidentOut)
async def investigate_incident(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident = await service.investigate(incident_id, tenant_id)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return incident


@router.post("/{incident_id}/monitor", dependencies=[_active_sub_guard], response_model=IncidentOut)
async def monitor_incident(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident = await service.monitor(incident_id, tenant_id)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return incident


@router.post("/{incident_id}/recover", dependencies=[_active_sub_guard], response_model=IncidentOut)
async def recover_incident(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident = await service.recover(incident_id, tenant_id)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return incident


@router.post("/{incident_id}/close", dependencies=[_active_sub_guard], response_model=IncidentOut)
async def close_incident(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident = await service.close(incident_id, tenant_id)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return incident


@router.post("/{incident_id}/archive", dependencies=[_active_sub_guard], response_model=IncidentOut)
async def archive_incident(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident = await service.archive(incident_id, tenant_id)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return incident


@router.post("/{incident_id}/resolve", dependencies=[_active_sub_guard], response_model=IncidentOut)
async def resolve_incident(
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    service: IncidentService = Depends(get_incident_service),
):
    try:
        incident = await service.resolve(incident_id, tenant_id)
    except IncidentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return incident
