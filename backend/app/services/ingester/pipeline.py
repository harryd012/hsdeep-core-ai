"""
Ingestion pipeline with usage counter injection.

Injects the TenantUsageCollector into the collection pipeline so that
every alert, metric, collector run, and API call is counted for metering
before being dispatched to downstream handlers.

The counter middleware is inserted before payload dispatch to guarantee
that usage is recorded even if a downstream handler fails — the counter
flush is independent of handler success/failure.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.metering.collector import default_collector
from app.models.usage import (
    EVENT_TYPE_ALERT_INGESTED,
    EVENT_TYPE_METRIC_INGESTED,
    EVENT_TYPE_COLLECTOR_RUN,
    EVENT_TYPE_API_CALL,
)


async def count_ingested_alert(
    tenant_id: uuid.UUID,
    db: AsyncSession,
    *,
    increment: int = 1,
) -> None:
    """Record an alert ingestion event for metering.

    Called from the alert service after an alert is upserted.
    """
    await default_collector.record(tenant_id, EVENT_TYPE_ALERT_INGESTED, db, increment)


async def count_ingested_metric(
    tenant_id: uuid.UUID,
    db: AsyncSession,
    *,
    increment: int = 1,
) -> None:
    """Record a metric ingestion event for metering.

    Called from the bundle saver after metrics are persisted.
    """
    await default_collector.record(tenant_id, EVENT_TYPE_METRIC_INGESTED, db, increment)


async def count_collector_run(
    tenant_id: uuid.UUID,
    db: AsyncSession,
    *,
    increment: int = 1,
) -> None:
    """Record a collector run event for metering.

    Called from the collection orchestrator after a run completes.
    """
    await default_collector.record(tenant_id, EVENT_TYPE_COLLECTOR_RUN, db, increment)


async def count_api_call(
    tenant_id: uuid.UUID,
    db: AsyncSession,
    *,
    increment: int = 1,
) -> None:
    """Record an API call event for metering.

    Called from middleware or route handlers.
    """
    await default_collector.record(tenant_id, EVENT_TYPE_API_CALL, db, increment)


async def flush_usage_counters(db: AsyncSession) -> None:
    """Explicitly flush all pending usage counters to the database.

    Should be called at the end of a request cycle or on a periodic
    timer to ensure counters are persisted.
    """
    await default_collector.flush(db)