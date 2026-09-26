"""Phase 9A: the scheduler tick is single-owner across concurrent workers.

These are real-DB tests of the EXISTING Postgres advisory-lock guard in
`app/scheduler/jobs.py` (no new mechanism was introduced): several API/worker
replicas may each fire a tick, but only the owner of the lock may run
collections.
"""
from __future__ import annotations

from sqlalchemy import text

from app.core.db import engine
from app.scheduler import jobs


async def _try_lock(conn) -> bool:
    result = await conn.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": jobs.TICK_LOCK_KEY}
    )
    return bool(result.scalar_one())


async def _unlock(conn) -> None:
    await conn.execute(
        text("SELECT pg_advisory_unlock(:key)"), {"key": jobs.TICK_LOCK_KEY}
    )


async def test_only_one_concurrent_worker_can_own_the_tick():
    async with engine.connect() as conn_a, engine.connect() as conn_b:
        assert await _try_lock(conn_a) is True, "first worker must become the owner"
        assert await _try_lock(conn_b) is False, (
            "a second worker must NOT be able to run the same tick"
        )
        await _unlock(conn_a)
        assert await _try_lock(conn_b) is True, (
            "ownership must transfer once the owner releases the lock"
        )
        await _unlock(conn_b)


async def test_tick_noops_when_another_worker_holds_the_lock(monkeypatch):
    """A non-owner tick must do nothing at all (no collection work)."""
    ran: list[bool] = []

    async def _fake_run_due_sources() -> None:
        ran.append(True)

    monkeypatch.setattr(jobs, "_run_due_sources", _fake_run_due_sources)

    async with engine.connect() as holder:
        assert await _try_lock(holder) is True
        await jobs.tick()  # must no-op
        await _unlock(holder)

    assert ran == [], "a non-owner tick must not run collections"


async def test_tick_runs_when_the_lock_is_free(monkeypatch):
    """Positive control: the owner does run the collection sweep."""
    ran: list[bool] = []

    async def _fake_run_due_sources() -> None:
        ran.append(True)

    monkeypatch.setattr(jobs, "_run_due_sources", _fake_run_due_sources)
    await jobs.tick()
    assert ran == [True], "the lock owner must run collections"

    # And the lock is released afterwards, so the next replica tick can own it.
    async with engine.connect() as other:
        assert await _try_lock(other) is True
        await _unlock(other)
