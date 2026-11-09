"""Retention: purge metric / sensor history, trace spans and raw events past their retention.

* Platform default: `metric_retention_days` for metrics (0 keeps everything,
  as before this job existed).
* Per tenant (security programme S5): a tenant's privacy settings may set its
  own metric and raw-event retention; that overrides the platform default for
  that tenant, longer or shorter.
* Trace spans (observability plan P8) follow the metric retention: they are
  telemetry of the same kind.

Deletes in bounded batches through the (tenant_id, time) indexes so one run
never holds a long lock; what is left is picked up by the next run.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.event import Event
from app.models.metric import Metric
from app.models.tenant_privacy import TenantPrivacySetting
from app.models.trace_span import TraceSpan

logger = logging.getLogger(__name__)

METRIC_RETENTION_INTERVAL_SECONDS = 3600
BATCH_SIZE = 10_000
MAX_BATCHES_PER_RUN = 50


async def _purge(db, model, time_col, *, days: int, now: datetime | None,
                 tenant_id: uuid.UUID | None = None, exclude: list[uuid.UUID] | None = None) -> int:
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    total = 0
    for _ in range(MAX_BATCHES_PER_RUN):
        ids = select(model.id).where(time_col < cutoff)
        if tenant_id is not None:
            ids = ids.where(model.tenant_id == tenant_id)
        if exclude:
            ids = ids.where(model.tenant_id.not_in(exclude))
        deleted = (await db.execute(delete(model).where(model.id.in_(ids.limit(BATCH_SIZE).scalar_subquery())))).rowcount or 0
        await db.commit()
        total += deleted
        if deleted < BATCH_SIZE:
            break
    return total


async def purge_old_metrics(db, *, days: int, now: datetime | None = None,
                            tenant_id: uuid.UUID | None = None, exclude: list[uuid.UUID] | None = None) -> int:
    """Delete metric rows recorded more than `days` ago; returns rows deleted."""
    return await _purge(db, Metric, Metric.recorded_at, days=days, now=now, tenant_id=tenant_id, exclude=exclude)


async def purge_old_spans(db, *, days: int, now: datetime | None = None,
                          tenant_id: uuid.UUID | None = None, exclude: list[uuid.UUID] | None = None) -> int:
    """Delete trace spans that started more than `days` ago; returns rows deleted."""
    return await _purge(db, TraceSpan, TraceSpan.start_time, days=days, now=now, tenant_id=tenant_id, exclude=exclude)


async def purge_old_events(db, *, days: int, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """Delete one tenant's raw events that occurred more than `days` ago."""
    return await _purge(db, Event, Event.occurred_at, days=days, now=now, tenant_id=tenant_id)


async def run_retention(db, now: datetime | None = None) -> dict[str, int]:
    """One retention pass: per-tenant settings first, then the platform default."""
    rows = (await db.execute(select(TenantPrivacySetting))).scalars().all()
    custom_metrics = [r.tenant_id for r in rows if r.metrics_retention_days]
    result = {"metrics": 0, "spans": 0, "events": 0}
    for r in rows:
        if r.metrics_retention_days:
            result["metrics"] += await purge_old_metrics(db, days=r.metrics_retention_days, now=now, tenant_id=r.tenant_id)
            result["spans"] += await purge_old_spans(db, days=r.metrics_retention_days, now=now, tenant_id=r.tenant_id)
        if r.events_retention_days:
            result["events"] += await purge_old_events(db, days=r.events_retention_days, now=now, tenant_id=r.tenant_id)
    result["metrics"] += await purge_old_metrics(db, days=get_settings().metric_retention_days, now=now, exclude=custom_metrics)
    result["spans"] += await purge_old_spans(db, days=get_settings().metric_retention_days, now=now, exclude=custom_metrics)
    return result


async def metric_retention_job() -> None:
    try:
        async with AsyncSessionLocal() as db:
            result = await run_retention(db)
        if any(result.values()):
            logger.info("Retention: deleted %s metric rows, %s trace spans and %s events.",
                        result["metrics"], result["spans"], result["events"])
    except Exception:  # noqa: BLE001 - a scheduler job must never crash the scheduler
        logger.exception("Retention job failed")
