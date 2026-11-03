"""
Sensor library routes (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P3 slice 1).

  GET  /api/monitoring/sensor-library                         templates + tenant usage
  POST /api/monitoring/sensor-library/templates/{key}/apply   set / reset user thresholds in bulk
  GET  /api/monitoring/sensor-library/recommendations         coverage rules per host (read-only)

Reads need `monitoring.read`; applying needs `monitoring.update` and an active
subscription. Every changed sensor is one audit event with old and new values.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.repositories.alert_repository import AlertRepository
from app.schemas.sensor_library import (
    ApplyTemplateIn,
    ApplyTemplateResult,
    RecommendationRuleOut,
    RecommendationsOut,
    RecommendedHostOut,
    SensorLibraryOut,
    SensorTemplateOut,
    UncataloguedMetricOut,
)
from app.services.alert_service import AlertService
from app.services.audit_service import AuditService
from app.services.monitoring.sensor_library import (
    MAX_APPLY_SENSORS,
    MAX_RECOMMENDATION_HOSTS,
    RULES,
    TEMPLATES,
    SensorLibraryService,
    TemplateNotFound,
    TooManySensors,
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
