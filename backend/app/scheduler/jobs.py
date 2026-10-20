"""
Scheduler tick job.

`tick()` is fired by `AsyncIOScheduler` (see `scheduler.py`) roughly once
every 60 seconds. It is guarded by a Postgres advisory lock so that if
`uvicorn` is ever scaled to more than one worker/replica, only one worker
actually runs the collection loop for a given tick - the others no-op and
return immediately.

Concurrency model: each due source's collection run gets its OWN
`AsyncSession` (checked out fresh inside `_run_bounded`). SQLAlchemy's
`AsyncSession` is not safe for concurrent use by multiple coroutines at
once, so sharing a single session across the bounded-concurrency `gather()`
below would be a real bug, not just a style preference - this is why
`_run_due_sources()` only reads `(id, tenant_id)` pairs out of its own
short-lived session before closing it, then each `_run_bounded()` task
re-fetches its own `MonitoringSource` through `MonitoringSourceRepository.get()`
(the existing, tenant-scoped method - no repository changes needed) inside
its own session.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import AsyncSessionLocal, engine
from app.repositories.alert_repository import AlertRepository
from app.repositories.collector_run_repository import CollectorRunRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.interface_repository import InterfaceRepository
from app.repositories.metric_repository import MetricRepository
from app.repositories.monitoring_source_repository import MonitoringSourceRepository
from app.repositories.plan_repository import PlanRepository
from app.repositories.sensor_repository import SensorRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.alert_service import AlertService
from app.services.billing_service import BillingService
from app.services.monitoring.bundle_saver import BundleSaver
from app.services.monitoring.collection_orchestrator import CollectionOrchestrator
from app.services.monitoring.rate_limiter import SourceRateLimiter, get_source_rate_limiter
from app.services.monitoring.source_health_manager import SourceHealthManager
from app.repositories.source_health_audit_repository import SourceHealthAuditRepository
from app.services.events.event_bus import get_event_bus
from app.services.rca_trigger import trigger_rca_for_incident

logger = logging.getLogger(__name__)


async def _audit_collection_skip(db, tenant_id: uuid.UUID, source_id: uuid.UUID) -> None:
    """Best-effort audit of a scheduler skip due to subscription status.
    Reuses the shared AuditService - never raises."""
    try:
        from app.models.knowledge_audit import AuditEvent  # noqa: F401 - ensure mapper registered
        from app.services.audit_service import AuditService

        await AuditService(db).record(
            tenant_id=tenant_id,
            action="collection.skipped_no_subscription",
            actor_type="system",
            entity_type="monitoring_source",
            entity_id=source_id,
            description="Collection skipped: tenant has no active subscription",
            context={"monitoring_source_id": str(source_id)},
        )
    except Exception:  # noqa: BLE001
        logger.debug("Audit of collection skip failed for source %s", source_id, exc_info=True)


async def _audit_collection_rate_limited(
    tenant_id: uuid.UUID, source_id: uuid.UUID
) -> None:
    """Best-effort audit of a skip caused by the per-source rate limiter
    (Phase 9A).

    Deliberately opens its OWN short-lived session: the rate-limit check happens
    before any collection session exists, and holding one open across the Redis
    round-trip would be worse. Never raises - losing an audit row must never
    turn into a lost collection.
    """
    try:
        from app.models.knowledge_audit import AuditEvent  # noqa: F401 - ensure mapper registered
        from app.services.audit_service import AuditService

        async with AsyncSessionLocal() as db:
            await AuditService(db).record(
                tenant_id=tenant_id,
                action="collection.skipped_rate_limited",
                actor_type="system",
                entity_type="monitoring_source",
                entity_id=source_id,
                description="Collection skipped: per-source rate limit exceeded",
                context={"monitoring_source_id": str(source_id)},
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.debug(
            "Audit of rate-limited skip failed for source %s", source_id, exc_info=True
        )

# Arbitrary constant advisory-lock key for this job - must stay stable
# across restarts/workers, since it's what makes only one worker run a
# given tick when uvicorn is scaled to more than one worker/replica.
TICK_LOCK_KEY = 91827364


async def tick() -> None:
    """One scheduler tick. Acquires the advisory lock, lists sources whose
    next_run_at <= now(), runs them with bounded concurrency, releases the
    lock. No-ops (does not raise) if the lock is held by another worker.

    Advisory locks are session/connection-scoped in Postgres, so the
    try-lock and unlock MUST happen on the same underlying connection - a
    single dedicated `AsyncConnection` is checked out from the engine for
    the lock's lifetime, separate from the per-source `AsyncSessionLocal`
    sessions used for the actual collection work below.
    """
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": TICK_LOCK_KEY})
        lock_acquired = bool(result.scalar_one())

        if not lock_acquired:
            logger.debug("Scheduler tick: advisory lock held by another worker, skipping.")
            return

        try:
            await _run_due_sources()
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": TICK_LOCK_KEY})


async def _run_due_sources() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        source_repo = MonitoringSourceRepository(db)
        due_sources = await source_repo.list_due(now)

        # Subscription-status filter (P1 remediation gap 5): sources belonging
        # to tenants with no ACTIVE subscription (suspended / canceled /
        # incomplete / none) are skipped this tick and the skip is audit-
        # logged. past_due keeps its grace period and still collects.
        from app.repositories.subscription_repository import SubscriptionRepository

        sub_repo = SubscriptionRepository(db)
        allowed_ids: set[uuid.UUID] = set()
        for tenant_id in {s.tenant_id for s in due_sources}:
            if await sub_repo.get_active(tenant_id) is not None:
                allowed_ids.add(tenant_id)

        skipped = [s for s in due_sources if s.tenant_id not in allowed_ids]
        for source in skipped:
            logger.warning(
                "Scheduler tick: skipping source_id=%s tenant_id=%s - no active "
                "subscription (suspended/canceled/incomplete).",
                source.id, source.tenant_id,
            )
            await _audit_collection_skip(db, source.tenant_id, source.id)
        await db.commit()

        # Read out the two scalar fields needed to re-fetch each source in
        # its own session below, before this session closes.
        due_ids = [(s.id, s.tenant_id) for s in due_sources if s.tenant_id in allowed_ids]

    if not due_ids:
        logger.debug("Scheduler tick: no due sources.")
        return

    logger.info("Scheduler tick: %s due source(s) found.", len(due_ids))

    semaphore = asyncio.Semaphore(settings.max_concurrent_collections)
    await asyncio.gather(
        *(_run_bounded(source_id, tenant_id, semaphore) for source_id, tenant_id in due_ids)
    )


async def _run_bounded(
    source_id: uuid.UUID,
    tenant_id: uuid.UUID,
    semaphore: asyncio.Semaphore,
    limiter: SourceRateLimiter | None = None,
) -> None:
    """Runs one source's collection cycle inside its own DB session, bounded
    by `semaphore`. Exceptions are caught and logged here rather than
    propagated - a crash collecting one source must never prevent others in
    the same tick from running, and must never abort `asyncio.gather()`
    for the rest. `CollectionOrchestrator.run()` already catches vendor-side
    failures internally; this try/except is a second, outer safety net for
    anything unexpected happening outside that (e.g. a DB connectivity
    blip while building the session itself).

    Phase 9A: the EXISTING per-source Redis rate limiter is consulted BEFORE a
    collection slot is consumed, so several worker replicas can never poll the
    same source concurrently. Disabled (rpm=0) it returns True immediately, and
    a Redis failure fails OPEN by design - rate limiting must never be the
    reason a source stops being polled.
    """
    limiter = limiter if limiter is not None else get_source_rate_limiter()
    if not await limiter.acquire(source_id):
        logger.info(
            "Scheduler tick: source_id=%s tenant_id=%s skipped by per-source "
            "rate limiter (rpm=%s).",
            source_id,
            tenant_id,
            limiter.rpm,
        )
        await _audit_collection_rate_limited(tenant_id, source_id)
        return

    async with semaphore:
        try:
            async with AsyncSessionLocal() as db:
                source_repo = MonitoringSourceRepository(db)
                source = await source_repo.get(source_id, tenant_id)
                if source is None:
                    # Deleted/moved between list_due() and now - not an error.
                    return

                orchestrator = CollectionOrchestrator(
                    run_repo=CollectorRunRepository(db),
                    saver=BundleSaver(
                        DeviceRepository(db),
                        InterfaceRepository(db),
                        SensorRepository(db),
                        MetricRepository(db),
                        EventRepository(db),
                        billing=BillingService(
                            plans=PlanRepository(db),
                            subscriptions=SubscriptionRepository(db),
                            db=db,
                        ),
                    ),
                    alert_service=AlertService(AlertRepository(db)),
                    source_repo=source_repo,
                    health_manager=SourceHealthManager(
                        source_repo,
                        SourceHealthAuditRepository(db),
                        get_event_bus(),
                    ),
                )
                outcome = await orchestrator.run(source, trigger="scheduler")
                await db.commit()
                # RCA activation: ONLY after the transaction above committed, so
                # a rolled-back collection can never emit AI_RCA_STARTED. One
                # trigger per Incident actually created during this run (zero,
                # one, or many). A trigger failure must not undo the committed
                # Incident, so it is logged and contained here.
                for reference in outcome.created_incidents:
                    try:
                        await trigger_rca_for_incident(
                            reference.incident_id, reference.tenant_id
                        )
                    except Exception:  # noqa: BLE001 - incident already committed
                        logger.exception(
                            "RCA trigger failed for committed incident %s",
                            reference.incident_id,
                        )
                # Source-health domain events, published AFTER commit so a
                # rolled-back collection never emits a spurious signal.
                for event in outcome.recovery_events:
                    try:
                        await get_event_bus().publish(event)
                    except Exception:  # noqa: BLE001 - health row already committed
                        logger.exception(
                            "Source-health event publish failed for source %s",
                            source_id,
                        )
        except Exception:  # noqa: BLE001 - never let one source's crash sink the tick
            logger.exception(
                "Scheduler tick: unexpected error running source_id=%s tenant_id=%s",
                source_id, tenant_id,
            )
