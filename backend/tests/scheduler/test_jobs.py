"""
Unit tests for `app.scheduler.jobs.tick()` (spec task 18.4).

Covers:
- the lock-not-acquired short-circuit (Requirement 5.3)
- the `poll_interval_seconds` due/not-due boundary via `list_due()`,
  exercised by calling `tick()` directly rather than a real
  `AsyncIOScheduler` (Requirement 5.2)

Runs against the real configured Postgres database (see
`tests/conftest.py`'s module docstring for why - advisory locks are
session/connection-scoped and cannot be meaningfully faked).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.db import AsyncSessionLocal, engine
from app.repositories.collector_run_repository import CollectorRunRepository
from app.scheduler import jobs

pytestmark = pytest.mark.asyncio


async def test_tick_skips_when_advisory_lock_held_by_another_worker(monkeypatch):
    """If another worker already holds TICK_LOCK_KEY, tick() must return
    without running the collection loop at all - simulated here by
    acquiring the same advisory lock on a second, independent connection
    before calling tick().

    A test-isolated lock key is used (TEST_TICK_LOCK_KEY) rather than the
    production TICK_LOCK_KEY (91827364), because the live backend server
    holds the production key for the duration of its scheduler cycle.
    Attempting to acquire a key already held by the server always returns
    False, which makes the "lock held by another worker" assertion vacuously
    true but prevents validating the actual skip-logic. Monkeypatching both
    `jobs.TICK_LOCK_KEY` and the holder connection to the same test-only
    constant guarantees isolation without any change to production behavior.
    """
    # A test-specific advisory lock key that the running server never uses.
    TEST_TICK_LOCK_KEY = 91827365  # distinct from production TICK_LOCK_KEY

    called = False

    async def _fail_if_called():
        nonlocal called
        called = True

    monkeypatch.setattr(jobs, "_run_due_sources", _fail_if_called)
    monkeypatch.setattr(jobs, "TICK_LOCK_KEY", TEST_TICK_LOCK_KEY)

    async with engine.connect() as holder_conn:
        result = await holder_conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": TEST_TICK_LOCK_KEY}
        )
        assert result.scalar_one() is True, (
            "Failed to acquire test advisory lock - another test may be holding it "
            "or key collision with a live process"
        )

        await jobs.tick()

        assert called is False, "tick() ran the collection loop despite the lock being held"

        await holder_conn.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": TEST_TICK_LOCK_KEY}
        )


async def test_tick_releases_lock_so_a_subsequent_tick_can_acquire_it():
    """Sanity check on the FINALLY-release behavior: after tick() returns
    (lock acquired, ran, released), a second, independent connection must
    be able to acquire the same key - proving tick() doesn't leak the lock
    even when `_run_due_sources()` finds nothing to do."""
    await jobs.tick()

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": jobs.TICK_LOCK_KEY}
        )
        acquired = result.scalar_one()
        if acquired:
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": jobs.TICK_LOCK_KEY})

    assert acquired is True, "tick() left the advisory lock held after returning"


async def test_tick_runs_due_source_and_skips_not_due_source(monitoring_source_factory, tenant):
    """`poll_interval_seconds` due/not-due boundary: a source with
    `last_sync_at=None` is always due; a source last synced 5 seconds ago
    with a 3600s poll interval is not. Verified end-to-end by calling the
    real `tick()` and inspecting the `CollectorRun` rows it produced - a
    `source_type="prtg"` row with no `hostname` configured will fail fast
    inside `CollectionOrchestrator` (CollectorConfigError), which is fine:
    we only care that a run was ATTEMPTED (a CollectorRun row exists) for
    the due source and NOT for the not-due one.
    """
    due_source = await monitoring_source_factory(last_sync_at=None, poll_interval_seconds=60)
    not_due_source = await monitoring_source_factory(
        last_sync_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        poll_interval_seconds=3600,
    )

    await jobs.tick()

    from app.core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        run_repo = CollectorRunRepository(db)
        due_runs = await run_repo.list(tenant.id, monitoring_source_id=due_source.id, limit=10)
        not_due_runs = await run_repo.list(
            tenant.id, monitoring_source_id=not_due_source.id, limit=10
        )

    assert len(due_runs) == 1, "expected exactly one CollectorRun attempt for the due source"
    assert len(not_due_runs) == 0, "not-due source should not have been run by this tick"
