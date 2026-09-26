"""
Unit test for the startup stale-run reconciliation sweep (spec task 18.7).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.repositories.collector_run_repository import CollectorRunRepository
from app.scheduler.scheduler import _reconcile_stale_runs

pytestmark = pytest.mark.asyncio


async def test_reconcile_marks_stale_running_run_as_error(monitoring_source_factory, tenant):
    source = await monitoring_source_factory()

    async with AsyncSessionLocal() as db:
        run_repo = CollectorRunRepository(db)
        run = await run_repo.create(tenant_id=tenant.id, monitoring_source_id=source.id, trigger="scheduler")
        # Force it old enough to exceed collector_run_stale_minutes (default 10).
        run.started_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        await db.flush()
        await db.commit()
        run_id = run.id

    await _reconcile_stale_runs()

    async with AsyncSessionLocal() as db:
        run_repo = CollectorRunRepository(db)
        runs = await run_repo.list(tenant.id, monitoring_source_id=source.id, limit=10)
        reconciled = next(r for r in runs if r.id == run_id)

    assert reconciled.status == "error"
    assert reconciled.error is not None and "stale" in reconciled.error.lower()


async def test_reconcile_leaves_fresh_running_run_untouched(monitoring_source_factory, tenant):
    source = await monitoring_source_factory()

    async with AsyncSessionLocal() as db:
        run_repo = CollectorRunRepository(db)
        run = await run_repo.create(tenant_id=tenant.id, monitoring_source_id=source.id, trigger="scheduler")
        run_id = run.id
        # started_at defaults to "now" in create() - well within the
        # staleness threshold, so this run must NOT be touched.
        await db.commit()

    await _reconcile_stale_runs()

    async with AsyncSessionLocal() as db:
        run_repo = CollectorRunRepository(db)
        runs = await run_repo.list(tenant.id, monitoring_source_id=source.id, limit=10)
        untouched = next(r for r in runs if r.id == run_id)

    assert untouched.status == "running"
    assert untouched.error is None
