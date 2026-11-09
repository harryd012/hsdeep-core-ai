"""
Dynamic baseline routes (docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P9 slice 2).

  GET /api/monitoring/sensors/{id}/baseline   the sensor's baseline: settings, state, expected band per hour
  PUT /api/monitoring/sensors/{id}/baseline   turn it on / off, direction, sensitivity (learns and judges at once)
  GET /api/monitoring/baselines               every baseline of the tenant, anomalous first

Reads need `monitoring.read`; changes need `monitoring.update` and an active
subscription, and are audited. Another tenant's sensor is a 404.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, get_tenant_id, require_permission
from app.core.db import get_db
from app.core.entitlements import require_active_subscription
from app.models.sensor import Sensor
from app.models.sensor_baseline import SensorBaseline
from app.services.audit_service import AuditService
from app.services.monitoring.baseline_math import MIN_SAMPLES, MIN_SPAN_DAYS, WINDOW_DAYS, Baseline, spread
from app.services.monitoring.baseline_service import BaselineService

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


class BaselineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    direction: str = Field("both", pattern="^(both|above|below)$")
    sensitivity: float = Field(3.0, ge=2.0, le=6.0)


class HourBand(BaseModel):
    hour: int
    expected: float
    low: float
    high: float
    samples: int
    #: hour | overall (too few samples in this hour)
    basis: str


class BaselineOut(BaseModel):
    sensor_id: uuid.UUID
    configured: bool
    enabled: bool
    direction: str
    sensitivity: float
    state: str
    ready: bool
    samples: int
    span_days: float
    window_days: int
    min_span_days: int
    min_samples: int
    last_value: float | None
    last_expected: float | None
    last_z: float | None
    last_evaluated_at: datetime | None
    computed_at: datetime | None
    hours: list[HourBand]


class BaselineListItem(BaseModel):
    sensor_id: uuid.UUID
    sensor_name: str
    unit: str | None
    enabled: bool
    state: str
    last_value: float | None
    last_expected: float | None
    last_z: float | None
    last_evaluated_at: datetime | None


async def _sensor(db: AsyncSession, tenant_id: uuid.UUID, sensor_id: uuid.UUID) -> Sensor:
    s = (await db.execute(select(Sensor).where(
        Sensor.id == sensor_id, Sensor.tenant_id == tenant_id, Sensor.is_deleted.is_(False)
    ))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Sensor not found")
    return s


def _out(sensor_id: uuid.UUID, row: SensorBaseline | None) -> BaselineOut:
    b = Baseline.from_json(row.stats if row else {})
    k = row.sensitivity if row else 3.0
    hours = []
    for h in range(24):
        stat, basis = b.expected(h)
        if stat is not None:
            w = spread(stat)
            hours.append(HourBand(hour=h, expected=stat.mean, low=stat.mean - k * w, high=stat.mean + k * w,
                                  samples=stat.n if basis == "hour" else 0, basis=basis))
    return BaselineOut(
        sensor_id=sensor_id, configured=row is not None, enabled=bool(row and row.enabled),
        direction=row.direction if row else "both", sensitivity=k, state=row.state if row else "off",
        ready=b.ready, samples=b.overall.n if b.overall else 0, span_days=b.span_days, window_days=WINDOW_DAYS,
        min_span_days=MIN_SPAN_DAYS, min_samples=MIN_SAMPLES,
        last_value=row.last_value if row else None, last_expected=row.last_expected if row else None,
        last_z=row.last_z if row else None, last_evaluated_at=row.last_evaluated_at if row else None,
        computed_at=row.computed_at if row else None, hours=hours if b.ready else [],
    )


@router.get("/sensors/{sensor_id}/baseline", response_model=BaselineOut,
            dependencies=[Depends(require_permission("monitoring.read"))])
async def get_baseline(sensor_id: uuid.UUID, tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    sensor = await _sensor(db, tenant_id, sensor_id)
    row = (await db.execute(select(SensorBaseline).where(SensorBaseline.sensor_id == sensor.id))).scalar_one_or_none()
    return _out(sensor.id, row)


@router.put("/sensors/{sensor_id}/baseline", response_model=BaselineOut,
            dependencies=[Depends(require_active_subscription)])
async def put_baseline(
    sensor_id: uuid.UUID, body: BaselineIn, tenant_id: uuid.UUID = Depends(get_tenant_id),
    principal: Principal = Depends(require_permission("monitoring.update")), db: AsyncSession = Depends(get_db),
):
    sensor = await _sensor(db, tenant_id, sensor_id)
    row, old, new = await BaselineService(db).configure(
        tenant_id, sensor, enabled=body.enabled, direction=body.direction, sensitivity=body.sensitivity,
    )
    if old != new:
        await AuditService(db).record_entity(
            tenant_id=tenant_id, action="sensor.baseline_updated", entity_type="sensor", entity_id=sensor.id,
            actor_id=principal.user_id, actor_email=principal.email,
            description=f"Dynamic baseline for sensor {sensor.name} {'turned on' if body.enabled else 'turned off'}"
            if old.get("enabled") != body.enabled else f"Dynamic baseline for sensor {sensor.name} changed",
            old_values=old, new_values=new,
        )
    await db.commit()
    await db.refresh(row)
    return _out(sensor.id, row)


@router.get("/baselines", response_model=list[BaselineListItem],
            dependencies=[Depends(require_permission("monitoring.read"))])
async def list_baselines(tenant_id: uuid.UUID = Depends(get_tenant_id), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(SensorBaseline, Sensor.name, Sensor.unit)
        .join(Sensor, Sensor.id == SensorBaseline.sensor_id)
        .where(SensorBaseline.tenant_id == tenant_id, Sensor.is_deleted.is_(False))
        .order_by(case((SensorBaseline.state == "anomalous", 0), else_=1), Sensor.name)
        .limit(1000)
    )).all()
    return [
        BaselineListItem(sensor_id=b.sensor_id, sensor_name=n, unit=u, enabled=b.enabled, state=b.state,
                         last_value=b.last_value, last_expected=b.last_expected, last_z=b.last_z,
                         last_evaluated_at=b.last_evaluated_at)
        for b, n, u in rows
    ]
