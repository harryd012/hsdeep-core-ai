"""Metric retention: purge metric / sensor history older than `metric_retention_days`.

Off by default (0 keeps everything, as before this job existed). Deletes in
bounded batches through the (tenant_id, recorded_at) index so one run never
holds a long lock; what is left is picked up by the next run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal
from app.models.metric import Metric

logger = logging.getLogger(__name__)

METRIC_RETENTION_INTERVAL_SECONDS = 3600
BATCH_SIZE = 10_000
MAX_BATCHES_PER_RUN = 50


async def purge_old_metrics(db, *, days: int, now: datetime | None = None) -> int:
    """Delete metric rows recorded more than `days` ago; returns rows deleted."""
    if days <= 0:
        return 0
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    total = 0
    for _ in range(MAX_BATCHES_PER_RUN):
        ids = select(Metric.id).where(Metric.recorded_at < cutoff).limit(BATCH_SIZE).scalar_subquery()
        deleted = (await db.execute(delete(Metric).where(Metric.id.in_(ids)))).rowcount or 0
        await db.commit()
        total += deleted
        if deleted < BATCH_SIZE:
            break
    return total


async def metric_retention_job() -> None:
    days = get_settings().metric_retention_days
    if days <= 0:
        return
    async with AsyncSessionLocal() as db:
        deleted = await purge_old_metrics(db, days=days)
    if deleted:
        logger.info("Metric retention: deleted %s rows older than %s days.", deleted, days)
