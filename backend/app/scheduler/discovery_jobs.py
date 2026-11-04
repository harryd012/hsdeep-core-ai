"""Scheduled discovery: probe every scope whose schedule has come round
(docs/design/OBSERVABILITY_PLATFORM_PLAN.md, P2).

Each due scope is probed through the same path as "probe now"
(``run_scope_probe``), so a scheduled run leaves the same durable probe job
for review. Nothing becomes inventory here: hosts are materialized only when an
operator approves them.

Bounded per tick (``MAX_SCOPES_PER_TICK``, probed one after another) and
guarded by an advisory lock so only one worker runs it. Scopes of tenants
without an active subscription are not probed; their next run moves on one
interval so they are not re-selected every tick.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.db import AsyncSessionLocal, engine
from app.repositories.discovery_probe_repository import DiscoveryProbeRepository
from app.repositories.discovery_scope_repository import DiscoveryScopeRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.discovery.schedule import next_run_after
from app.services.discovery.scope_probe_service import ScopeProbeFailed, run_scope_probe
from app.services.discovery.scope_runner import PROBEABLE_SCOPE_TYPES, ScopeNotProbeable

logger = logging.getLogger(__name__)

DISCOVERY_SCHEDULE_INTERVAL_SECONDS = 60
MAX_SCOPES_PER_TICK = 5
# Stable advisory-lock key for this job (distinct from the collection tick's).
DISCOVERY_LOCK_KEY = 91827365


async def run_due_scopes(now: datetime | None = None) -> int:
    """Probe the scopes that are due; returns how many were probed."""
    now = now or datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        repo = DiscoveryScopeRepository(db)
        due = await repo.list_due(now, PROBEABLE_SCOPE_TYPES, MAX_SCOPES_PER_TICK)
        subs = SubscriptionRepository(db)
        active = {t for t in {s.tenant_id for s in due} if await subs.get_active(t) is not None}
        for scope in due:
            if scope.tenant_id not in active:
                logger.warning(
                    "Scheduled discovery: skipping scope_id=%s tenant_id=%s - no active subscription.",
                    scope.id, scope.tenant_id,
                )
                await repo.update(scope, next_run_at=next_run_after(scope.schedule, now))
        await db.commit()
        due_ids = [(s.id, s.tenant_id) for s in due if s.tenant_id in active]

    probed = 0
    for scope_id, tenant_id in due_ids:
        # One session per scope: a failure in one never rolls back another.
        async with AsyncSessionLocal() as db:
            repo = DiscoveryScopeRepository(db)
            scope = await repo.get(scope_id, tenant_id)
            if scope is None:
                continue
            try:
                await run_scope_probe(repo, DiscoveryProbeRepository(db), scope, trigger="scheduler", now=now)
                probed += 1
            except ScopeProbeFailed:
                probed += 1  # recorded as a failed job; next run already set
            except ScopeNotProbeable:
                await repo.update(scope, next_run_at=None)
            await db.commit()
    return probed


async def discovery_schedule_job() -> None:
    async with engine.connect() as conn:
        locked = bool(
            (await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": DISCOVERY_LOCK_KEY})).scalar_one()
        )
        if not locked:
            return
        try:
            probed = await run_due_scopes()
            if probed:
                logger.info("Scheduled discovery: probed %s scope(s).", probed)
        except Exception:  # noqa: BLE001 - the next tick retries
            logger.exception("Scheduled discovery tick failed.")
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": DISCOVERY_LOCK_KEY})
