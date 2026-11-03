"""
Sensor library routes (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P3 slice 1).

  GET  /api/monitoring/sensor-library                         templates + tenant usage
  POST /api/monitoring/sensor-library/templates/{key}/apply   set / reset user thresholds in bulk
  GET  /api/monitoring/sensor-library/recommendations         coverage rules per host (read-only)
  POST /api/monitoring/sensor-library/reachability            add tied reachability checks (P3 slice 2)

Reads need `monitoring.read`; applying needs `monitoring.update` and an active
subscription. Every changed sensor is one audit event with old and new values.
Adding checks needs `monitoring.create`, an active subscription and room in the
plan's Sources limit for all of them; every created source is audited.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import evaluate_resource_limit, get_billing_service, require_active_subscription
from app.repositories.alert_repository import AlertRepository
from app.schemas.sensor_library import (
    ApplyTemplateIn,
    ApplyTemplateResult,
    ReachabilityCreated,
    ReachabilityIn,
    ReachabilityResult,
    RecommendationRuleOut,
    RecommendationsOut,
    RecommendedHostOut,
    SensorLibraryOut,
    SensorTemplateOut,
    UncataloguedMetricOut,
)
from app.services.alert_service import AlertService
from app.services.audit_service import AuditService
from app.services.billing_service import BillingService
from app.services.monitoring.sensor_library import (
    MAX_APPLY_SENSORS,
    MAX_RECOMMENDATION_HOSTS,
    RULES,
    TEMPLATES,
    SensorLibraryService,
    TemplateNotFound,
    TooManySensors,
    check_reachability_request,
    default_thresholds,
)

router = APIRouter(prefix="/monitoring/sensor-library", tags=["sensor-library"])


def _jsonable(value):
    return value.isoformat() if isinstance(value, datetime) else value


@router.get("", response_model=SensorLibraryOut, dependencies=[Depends(require_permission("monitoring.read"))])
async def get_library(
    tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)
) -> SensorLibraryOut:
    usage, uncatalogued = await SensorLibraryService(db).usage(tenant_id)
    templates = []
    for t in TEMPLATES:
        u = usage.get(t.key, {})
        warning, critical, origin = default_thresholds(t)
        templates.append(SensorTemplateOut(
            key=t.key, name=t.name, category=t.category, description=t.description,
            source_types=list(t.source_types), metric_types=list(t.metric_types), unit=t.unit,
            direction=t.direction, thresholds_applicable=t.thresholds_applicable,
            default_warning=warning, default_critical=critical, default_origin=origin,
            sensors=u.get("total", 0), by_status=dict(u.get("by_status", {})),
            user_thresholds=u.get("user_thresholds", 0), reporting_source_types=sorted(u.get("source_types", ())),
        ))
    return SensorLibraryOut(templates=templates, uncatalogued=[UncataloguedMetricOut(**o) for o in uncatalogued])


@router.post(
    "/templates/{key}/apply", response_model=ApplyTemplateResult,
    dependencies=[Depends(require_active_subscription)],
)
async def apply_template(
    key: str,
    body: ApplyTemplateIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("monitoring.update")),
    db: AsyncSession = Depends(get_db),
) -> ApplyTemplateResult:
    """Apply the template's thresholds to its sensors (all, or those of `device_ids`).
    Status and alerts follow at once, exactly as for one sensor."""
    if (body.thresholds is None) == (not body.reset):
        raise HTTPException(status_code=422, detail="Send thresholds, or reset: true")
    service = SensorLibraryService(db)
    try:
        changed, matched, not_found = await service.apply(
            tenant_id, key, None if body.reset else body.thresholds, body.device_ids, dry_run=body.dry_run
        )
    except TemplateNotFound as exc:
        raise HTTPException(status_code=404, detail="Template not found") from exc
    except TooManySensors as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{exc.args[0]} sensors match; apply to at most {MAX_APPLY_SENSORS} at a time (choose hosts)",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    status_changed = 0
    if changed:
        alerts = AlertService(AlertRepository(db))
        audit = AuditService(db)
        for sensor, old, new in changed:
            if "status" in new:
                status_changed += 1
                await alerts.evaluate_sensor(sensor)
            await audit.record_entity(
                tenant_id=tenant_id, action="sensor.template_applied", entity_type="sensor", entity_id=sensor.id,
                actor_id=principal.user_id, actor_email=principal.email,
                description=f"Template {key} {'reset' if body.reset else 'applied'} on sensor {sensor.name}",
                old_values={k: _jsonable(v) for k, v in old.items()},
                new_values={k: _jsonable(v) for k, v in new.items()},
            )
        await db.commit()
    return ApplyTemplateResult(
        template=key, matched=matched, changed=len(changed), status_changed=status_changed,
        not_found=not_found, dry_run=body.dry_run,
    )


@router.get(
    "/recommendations", response_model=RecommendationsOut,
    dependencies=[Depends(require_permission("monitoring.read"))],
)
async def get_recommendations(
    rule: str | None = Query(None, description="Only hosts this rule holds for"),
    limit: int = Query(100, ge=1, le=MAX_RECOMMENDATION_HOSTS),
    offset: int = Query(0, ge=0),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
) -> RecommendationsOut:
    if rule is not None and rule not in RULES:
        raise HTTPException(status_code=422, detail=f"Unknown rule {rule!r}")
    hosts = await SensorLibraryService(db).recommendations(tenant_id)
    counts = {k: 0 for k in RULES}
    for h in hosts:
        for r in h["rules"]:
            counts[r] += 1
    selected = [h for h in hosts if rule is None or rule in h["rules"]]
    page = selected[offset: offset + limit]
    return RecommendationsOut(
        rules=[RecommendationRuleOut(key=r.key, title=r.title, description=r.description, action=r.action, hosts=counts[r.key])
               for r in RULES.values()],
        hosts=[RecommendedHostOut(**h) for h in page],
        total=len(selected),
        truncated=offset + limit < len(selected),
    )


async def _sources_available(billing: BillingService, tenant_id: uuid.UUID) -> int | None:
    """Sources the tenant's plan still allows (None: no limit on the plan)."""
    entitlements = await billing.get_entitlements(tenant_id)
    for limit in entitlements["limits"]:
        if limit["resource"] == "collectors":
            return max(0, limit["limit"] - limit["used"])
    return None


@router.post(
    "/reachability", response_model=ReachabilityResult,
    dependencies=[Depends(require_active_subscription)],
)
async def add_reachability(
    body: ReachabilityIn,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("monitoring.create")),
    db: AsyncSession = Depends(get_db),
    billing: BillingService = Depends(get_billing_service),
) -> ReachabilityResult:
    """One ICMP or TCP check per host, tied to the host so its readings land on
    it (no duplicate device). Hosts already checked, without an address or not
    in the tenant are skipped and listed. The checks run from the platform's
    collection service, so the hosts must be reachable from it."""
    try:
        check_reachability_request(body.check, body.port, body.poll_interval_seconds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    service = SensorLibraryService(db)
    plan = await service.plan_reachability(tenant_id, body.device_ids)
    available = await _sources_available(billing, tenant_id)

    created = []
    if not body.dry_run and plan.to_create:
        denial = await evaluate_resource_limit(billing, tenant_id, "collectors")
        if denial is not None or (available is not None and len(plan.to_create) > available):
            payload = denial[1] if denial is not None else {
                "error": "plan_limit_exceeded",
                "resource": "collectors",
                "message": (
                    f"{len(plan.to_create)} checks need {len(plan.to_create)} sources; "
                    f"your plan allows {available} more. Choose fewer hosts or upgrade."
                ),
                "upgrade_url": "/usage",
            }
            raise HTTPException(status_code=402, detail=payload)
        sources = await service.create_reachability(
            tenant_id, plan.to_create, check=body.check, port=body.port,
            poll_interval_seconds=body.poll_interval_seconds,
        )
        audit = AuditService(db)
        for device, source in zip(plan.to_create, sources):
            await audit.record_entity(
                tenant_id=tenant_id, action="monitoring_source.reachability_added", entity_type="monitoring_source",
                entity_id=source.id, actor_id=principal.user_id, actor_email=principal.email,
                description=f"Reachability check ({body.check.upper()}) added for host {device.name}",
                new_values={
                    "name": source.name, "source_type": "connectivity", "hostname": source.hostname,
                    "device_id": str(device.id), "check": body.check, "port": body.port,
                    "poll_interval_seconds": body.poll_interval_seconds,
                },
            )
            created.append(ReachabilityCreated(device_id=device.id, source_id=source.id, name=source.name))
        await db.commit()
        if available is not None:
            available -= len(created)

    return ReachabilityResult(
        requested=len(dict.fromkeys(body.device_ids)),
        to_create=[d.id for d in plan.to_create],
        created=created,
        already_checked=plan.already_checked,
        no_address=plan.no_address,
        not_found=plan.not_found,
        sources_available=available,
        dry_run=body.dry_run,
    )
