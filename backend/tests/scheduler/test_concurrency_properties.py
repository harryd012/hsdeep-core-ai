"""
Property-based tests for the Scheduler (spec tasks 18.5, 18.6).

Uses `hypothesis` per design.md's Property-Based Testing Approach. Runs
against the real configured Postgres database (advisory locks are
session/connection-scoped and cannot be meaningfully faked - see
tests/conftest.py's module docstring).

Note on hypothesis + asyncio on this platform: hypothesis calls a test
function synchronously once per generated example; mixing that with
pytest-asyncio's per-test coroutine model is fragile. These tests drive
each hypothesis example via `_run_async_case()`, which runs the example on
its OWN fresh event loop.

Why a fresh engine per example (not a shared pool dispose):
- pytest-asyncio 0.26 binds the shared `app.core.db.engine` to the
  session-scoped event loop the first time it is used. Hypothesis creates
  a brand-new event loop per example via `asyncio.new_event_loop()`.
- If the shared engine is reused on the new loop, asyncpg raises "Task got
  Future attached to a different loop" because the pool's connections are
  bound to the session loop.
- `engine.dispose()` only flushes idle pool connections; it cannot prevent
  new connections allocated on the new loop from corrupting the shared pool
  state when the new loop closes and those connections are never properly
  returned.
- Creating a completely independent engine instance per example means no
  asyncpg connection ever crosses event-loop boundaries. The example engine
  and all its connections are created, used, and disposed within the new
  loop; the shared session-loop engine is left completely untouched.
- `_run_async_case` patches `app.core.db`, `app.scheduler.jobs`, and this
  module's own imported `AsyncSessionLocal`/`engine` names for the duration
  of each example, then restores originals in a `finally` block.

Each example still creates and tears down its own tenant/source fixtures
directly (rather than via the function-scoped `tenant`/
`monitoring_source_factory` fixtures, which hypothesis's calling
convention isn't compatible with).
"""
from __future__ import annotations

import asyncio
import uuid

from hypothesis import given, settings as hyp_settings, strategies as st
from sqlalchemy import delete

from app.collectors.base import BaseCollector, CollectorRegistry, ConnectionCheck, NormalizedBundle
from app.core.config import get_settings
from app.core.db import AsyncSessionLocal, engine
from app.models.monitoring_source import MonitoringSource
from app.models.tenant import Tenant
from app.scheduler import jobs


def _run_async_case(case_factory):
    """Run one hypothesis example on its own fresh event loop with its own
    isolated engine and session factory.

    Why a fresh engine per example (not a shared pool dispose):
    - pytest-asyncio 0.26 binds the shared `app.core.db.engine` to the
      session-scoped event loop the first time it is used. Hypothesis runs
      each example in a brand-new event loop via `asyncio.new_event_loop()`.
    - If the shared engine is reused, asyncpg raises "Task got Future attached
      to a different loop" when its connections are checked out/closed on the
      new loop.
    - `engine.dispose()` only closes idle pool connections - it cannot prevent
      new connections on the new loop from being mis-attributed to the shared
      pool that will later be used by the session loop.
    - Creating a completely independent engine per example means no asyncpg
      connection ever crosses event-loop boundaries. The example's engine and
      all its connections are created, used, and disposed within the same new
      loop, then the shared engine (session loop) is completely untouched.

    The example's engine and session factory are injected into both
    `app.core.db` and `app.scheduler.jobs` for the duration of the case, then
    restored, so all code paths that import from those modules see the
    isolated engine without any permanent module-level change.
    """
    import sys
    import app.core.db as _db_module
    import app.scheduler.jobs as _jobs_module
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import get_settings as _get_settings

    # This test module itself has `AsyncSessionLocal` and `engine` as module-level
    # names (imported from app.core.db). Those names are used by _make_tenant_with_sources
    # and _cleanup_tenant. We need to patch this module's globals too.
    _this_module = sys.modules[__name__]

    async def _with_isolated_engine():
        settings = _get_settings()
        example_engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
            pool_size=5,
            max_overflow=5,
        )
        example_session_factory = async_sessionmaker(
            bind=example_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Patch both modules so `tick()` and all repository calls inside the
        # example use the per-example engine, not the shared session-loop engine.
        original_db_engine = _db_module.engine
        original_db_session = _db_module.AsyncSessionLocal
        original_jobs_engine = _jobs_module.engine
        original_jobs_session = _jobs_module.AsyncSessionLocal
        # Also patch the test module's own imported names used by fixture helpers.
        original_this_engine = getattr(_this_module, "engine", None)
        original_this_session = getattr(_this_module, "AsyncSessionLocal", None)

        _db_module.engine = example_engine
        _db_module.AsyncSessionLocal = example_session_factory
        _jobs_module.engine = example_engine
        _jobs_module.AsyncSessionLocal = example_session_factory
        _this_module.engine = example_engine
        _this_module.AsyncSessionLocal = example_session_factory
        try:
            return await case_factory()
        finally:
            _db_module.engine = original_db_engine
            _db_module.AsyncSessionLocal = original_db_session
            _jobs_module.engine = original_jobs_engine
            _jobs_module.AsyncSessionLocal = original_jobs_session
            if original_this_engine is not None:
                _this_module.engine = original_this_engine
            if original_this_session is not None:
                _this_module.AsyncSessionLocal = original_this_session
            # Dispose all example connections on this loop before the loop closes.
            await example_engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_with_isolated_engine())
    finally:
        loop.close()

_FAKE_SOURCE_TYPE = "test_fake_concurrency"


class _ConcurrencyTrackingCollector(BaseCollector):
    """Fake collector whose collect() tracks how many instances are
    executing simultaneously - the signal Property 4 (bounded tick
    concurrency) needs. Registered/unregistered per-test via
    `_register_fake_collector()`/`_unregister_fake_collector()` rather than
    the real `@CollectorRegistry.register(...)` decorator, since this must
    be cleanly removable and never leak into other tests' registry state.
    """

    display_name = "Test Fake Concurrency Collector"
    implemented = True

    active_count = 0
    max_active_seen = 0

    async def authenticate(self) -> ConnectionCheck:
        return ConnectionCheck(success=True, message="ok")

    async def collect(self):
        cls = type(self)
        cls.active_count += 1
        cls.max_active_seen = max(cls.max_active_seen, cls.active_count)
        try:
            await asyncio.sleep(0.05)
        finally:
            cls.active_count -= 1
        return {}

    def normalize(self, raw):
        return NormalizedBundle()


def _register_fake_collector() -> None:
    _ConcurrencyTrackingCollector.source_type = _FAKE_SOURCE_TYPE
    CollectorRegistry._collectors[_FAKE_SOURCE_TYPE] = _ConcurrencyTrackingCollector


def _unregister_fake_collector() -> None:
    CollectorRegistry._collectors.pop(_FAKE_SOURCE_TYPE, None)


async def _make_tenant_with_sources(n: int) -> tuple[Tenant, list[MonitoringSource]]:
    from datetime import datetime, timedelta, timezone

    from app.models.plan import Plan
    from app.models.subscription import Subscription

    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="Prop Test Tenant", slug=f"prop-test-{uuid.uuid4().hex[:8]}")
        db.add(tenant)
        await db.flush()

        # The scheduler skips tenants without an ACTIVE subscription; seed one.
        plan = Plan(
            name=f"Prop-{uuid.uuid4().hex[:8]}",
            tier=f"prop-{uuid.uuid4().hex[:8]}",
            description=None,
            price_monthly=0,
            price_annual=0,
            device_limit=1000,
            sensor_limit=10000,
            api_rate_limit=100000,
            collector_limit=100,
            seats=25,
            features_json={},
            is_public=False,
        )
        db.add(plan)
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add(Subscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            billing_interval="month",
        ))

        sources = [
            MonitoringSource(
                tenant_id=tenant.id,
                name=f"fake-source-{uuid.uuid4().hex[:8]}",
                source_type=_FAKE_SOURCE_TYPE,
                is_active=True,
                poll_interval_seconds=60,
                status="pending",
                last_sync_at=None,  # always due
            )
            for _ in range(n)
        ]
        db.add_all(sources)
        await db.commit()

    return tenant, sources


async def _cleanup_tenant(tenant: Tenant) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        await db.commit()


async def _run_bounded_concurrency_case(n_sources: int, max_concurrent: int) -> int:
    """Creates `n_sources` due sources (all registered under the fake
    collector), runs exactly one `tick()` with `max_concurrent_collections`
    overridden to `max_concurrent`, and returns the maximum number of
    concurrently-active `collect()` calls observed during that tick."""
    _ConcurrencyTrackingCollector.active_count = 0
    _ConcurrencyTrackingCollector.max_active_seen = 0

    tenant, _sources = await _make_tenant_with_sources(n_sources)
    try:
        fake_settings = get_settings().model_copy(
            update={"max_concurrent_collections": max_concurrent}
        )
        original_get_settings = jobs.get_settings
        jobs.get_settings = lambda: fake_settings
        try:
            await jobs.tick()
        finally:
            jobs.get_settings = original_get_settings
    finally:
        await _cleanup_tenant(tenant)

    return _ConcurrencyTrackingCollector.max_active_seen


@hyp_settings(max_examples=5, deadline=None)
@given(n_sources=st.integers(min_value=2, max_value=8))
def test_bounded_tick_concurrency_never_exceeds_configured_max(n_sources):
    """Property 4: Tick concurrency never exceeds the configured bound -
    for arbitrary generated numbers of due sources, the number of in-flight
    collect() calls at any instant never exceeds MAX_CONCURRENT_COLLECTIONS.
    """
    _register_fake_collector()
    try:
        max_concurrent = 3
        max_seen = _run_async_case(
            lambda: _run_bounded_concurrency_case(n_sources, max_concurrent)
        )
    finally:
        _unregister_fake_collector()

    assert max_seen >= 1, "expected at least one collect() call to have run"
    assert max_seen <= max_concurrent, (
        f"observed {max_seen} concurrent collect() calls, exceeding the "
        f"configured bound of {max_concurrent}"
    )


# -- Property 5: single active tick per moment --------------------------

_tick_state = {"active": 0, "max_active": 0}


async def _instrumented_run_due_sources() -> None:
    _tick_state["active"] += 1
    _tick_state["max_active"] = max(_tick_state["max_active"], _tick_state["active"])
    try:
        await asyncio.sleep(0.05)
    finally:
        _tick_state["active"] -= 1


async def _run_single_active_tick_case(n_concurrent_ticks: int) -> int:
    """Fires `n_concurrent_ticks` simulated concurrent tick() invocations
    (as separate coroutines, each opening its own connection for the
    advisory lock attempt, mirroring separate worker processes) and
    returns the max number that were ever inside the locked section
    simultaneously."""
    _tick_state["active"] = 0
    _tick_state["max_active"] = 0

    original = jobs._run_due_sources
    jobs._run_due_sources = _instrumented_run_due_sources
    try:
        await asyncio.gather(*(jobs.tick() for _ in range(n_concurrent_ticks)))
    finally:
        jobs._run_due_sources = original

    return _tick_state["max_active"]


@hyp_settings(max_examples=5, deadline=None)
@given(n_concurrent=st.integers(min_value=2, max_value=6))
def test_single_active_tick_per_moment(n_concurrent):
    """Property 5: At most one active tick per moment across all workers -
    for arbitrary generated numbers of simulated concurrent tick()
    invocations, no two ever hold the advisory lock and run their
    collection loop simultaneously."""
    max_seen = _run_async_case(lambda: _run_single_active_tick_case(n_concurrent))
    assert max_seen <= 1, f"observed {max_seen} ticks running concurrently, expected at most 1"
