"""
Shared pytest fixtures.

Per design.md's Testing Strategy ("existing project convention — no
in-memory SQLite substitute, since JSONB/partial-unique-index behavior is
Postgres-specific"), these tests run against the real configured Postgres
database (`Settings.database_url`, i.e. `backend/.env`'s `DATABASE_URL`).

Isolation strategy: `app.scheduler.jobs.tick()` deliberately opens its OWN
independent `AsyncSession` per source (see that module's docstring - this
is required because SQLAlchemy sessions aren't safe to share across
concurrent coroutines). That means a single borrowed connection/transaction
fixture can't observe or roll back what `tick()` writes - there is no
single "outer transaction" to rollback around code that opens its own
sessions/connections. Fixtures here instead create real rows through a
short-lived session (committed) and explicitly delete them again in
teardown, scoped to a unique per-test `Tenant` so cleanup is a single
cascade delete regardless of how many child rows a test's code created.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.models.monitoring_source import MonitoringSource
from app.models.tenant import Tenant


# ---------------------------------------------------------------------------
# pytest-asyncio version guard (TEST INFRASTRUCTURE ONLY - no product code)
# ---------------------------------------------------------------------------
# pyproject.toml sets `asyncio_default_test_loop_scope = "session"`, which is
# only registered by pytest-asyncio >= 0.26. On an older install that option is
# ignored (emitting "Unknown config option") and async TESTS fall back to
# per-function loops, so the module-level `app.core.db.engine` asyncpg pool -
# bound to the first loop that used it - is then accessed from a second loop
# and every DB test fails with "attached to a different loop". Fail fast here
# with an actionable message instead of that cryptic runtime error.
_pytest_asyncio_version = tuple(
    int(x) for x in getattr(pytest_asyncio, "__version__", "0.0").split(".")[:2]
)
if _pytest_asyncio_version < (0, 26):
    raise RuntimeError(
        "pytest-asyncio >= 0.26 is required: the session-scoped test loop "
        "(asyncio_default_test_loop_scope) is unsupported in "
        f"pytest-asyncio {getattr(pytest_asyncio, '__version__', '?')}. "
        "Run the suite under the pinned virtualenv."
    )


@pytest.fixture(scope="session", autouse=True)
def _refuse_dev_database_for_tests():
    """Fail fast rather than let DB-writing tests mutate the dev database.

    These fixtures COMMIT real rows (Tenant / MonitoringSource / CollectorRun /
    Incident ...) and rely on teardown to delete them; a killed run leaves
    orphans in the dev DB (observed: 2,420 tenants). Require an explicit
    TEST_DATABASE_URL so tests bind to a dedicated throwaway database instead
    (see app.core.db: the engine selects ``test_database_url`` when set).
    """
    from app.core.config import get_settings

    if get_settings().test_database_url is None:
        pytest.exit(
            "Refusing to run tests against the dev database: TEST_DATABASE_URL "
            "is unset. Set it to a dedicated test database (e.g. hsdeep_test).",
            returncode=1,
        )


# Event-loop policy: pytest-asyncio >= 0.26 runs BOTH async fixtures and
# async tests on a single session-scoped loop (configured in pyproject.toml
# via `asyncio_default_fixture_loop_scope` / `asyncio_default_test_loop_scope`).
# A session loop is required because `app.core.db.engine` is a single
# module-level object created once at import time - its pooled asyncpg
# connections get bound to whatever event loop first uses them, so per-test
# loops fail every subsequent test with "Event loop is closed" / "attached to
# a different loop".
#
# The deprecated session-scoped `event_loop` fixture override that used to
# live here was REMOVED: under pytest-asyncio 0.24 it put async TESTS on the
# override's loop while async FIXTURES followed
# asyncio_default_fixture_loop_scope - two different session loops - which is
# exactly what broke the DB-fixture suites (test_repositories, test_task_service, ...).


@pytest_asyncio.fixture
async def tenant():
    """A real, committed `Tenant` row, deleted (cascades to any
    MonitoringSource/CollectorRun rows created under it, per the models'
    `ondelete="CASCADE"` foreign keys) at teardown."""
    async with AsyncSessionLocal() as db:
        t = Tenant(name="Test Tenant", slug=f"test-{uuid.uuid4().hex[:8]}")
        db.add(t)
        await db.commit()
        await db.refresh(t)

    yield t

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == t.id))
        await db.commit()


@pytest_asyncio.fixture
async def seeded_subscription(tenant: Tenant):
    """An active Plan + Subscription committed for the shared `tenant`.

    Write routes are gated by require_active_subscription / plan entitlements
    (P1 remediation), so integration tests that exercise POST/PATCH/DELETE
    endpoints need their tenant to hold an active subscription. Tests that
    specifically verify gate behaviour must NOT use this fixture.
    Teardown deletes subscription then plan (tenant cascade handles rows
    created through the API).
    """
    from datetime import datetime, timedelta, timezone

    from app.models.plan import Plan
    from app.models.subscription import Subscription

    async with AsyncSessionLocal() as db:
        plan = Plan(
            name=f"Fixture-{uuid.uuid4().hex[:8]}",
            tier=f"fixture-{uuid.uuid4().hex[:8]}",
            description="autouse-ish test plan",
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
        sub = Subscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            billing_interval="month",
        )
        db.add(sub)
        await db.commit()
        await db.refresh(sub)

    yield sub

    async with AsyncSessionLocal() as db:
        await db.execute(delete(Subscription).where(Subscription.tenant_id == tenant.id))
        await db.execute(delete(Plan).where(Plan.id == plan.id))
        await db.commit()


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """A short-lived AsyncSession that is rolled back after the test.

    Unlike the `tenant` fixture which commits, this fixture lets the
    test control commit/rollback and auto-rolls back at teardown so
    tests are isolated without needing explicit cleanup.
    """
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
def monitoring_source_factory(tenant: Tenant):
    """Factory fixture: `await monitoring_source_factory(**overrides)`
    creates and commits a `MonitoringSource` row scoped to `tenant`
    (deleted automatically via cascade when `tenant` tears down)."""

    async def _make(**overrides) -> MonitoringSource:
        fields = {
            "tenant_id": tenant.id,
            "name": f"test-source-{uuid.uuid4().hex[:8]}",
            "source_type": "prtg",
            "is_active": True,
            "poll_interval_seconds": 60,
            "status": "pending",
        }
        fields.update(overrides)
        async with AsyncSessionLocal() as db:
            source = MonitoringSource(**fields)
            db.add(source)
            await db.commit()
            await db.refresh(source)
        return source

    return _make


# ---------------------------------------------------------------------------
# Event-loop integrity guard (TEST INFRASTRUCTURE ONLY - no product code)
# ---------------------------------------------------------------------------
# pytest-asyncio 0.26 runs async tests through `wrap_in_sync`, which obtains the
# *current* event loop via `asyncio.get_event_loop()`. A sync test that drives
# coroutines with `asyncio.run()` - a valid, widely used pattern in the RCA
# lifecycle/trigger/topology suites - ends with `events.set_event_loop(None)`,
# which clears that process-global pointer. A later async test in the SAME
# process then dies with:
#
#   RuntimeError: There is no current event loop in thread 'MainThread'.
#   (pytest_asyncio/plugin.py -> _get_event_loop_no_warn -> asyncio.get_event_loop)
#
# That is precisely why these tests passed standalone but failed in a mixed
# selection: the tests are correct, only the pointer was lost. These fixtures
# remember the session loop the plugin created and re-install THAT SAME loop
# when it has been cleared, so `asyncio.run()`-style and async-style suites can
# coexist in one run.
#
# Deliberately never a NEW loop: a fresh loop would re-bind
# `app.core.db.engine`'s pooled asyncpg connections to a second loop and break
# the DB suites with "attached to a different loop" (see the policy note above).
# Restoration happens ONLY when no current loop exists, so a suite that
# legitimately sets its own loop is never overridden.

_SESSION_LOOP: dict[str, asyncio.AbstractEventLoop] = {}


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _remember_session_event_loop() -> None:
    """Record the event loop pytest-asyncio created for this session."""
    _SESSION_LOOP["loop"] = asyncio.get_running_loop()


@pytest.fixture(autouse=True)
def _restore_session_event_loop() -> None:
    """Re-install the session loop if a previous test's `asyncio.run()` cleared it."""
    loop = _SESSION_LOOP.get("loop")
    if loop is None or loop.is_closed():
        return
    policy = asyncio.get_event_loop_policy()
    try:
        # Policy accessor (not asyncio.get_event_loop) so no DeprecationWarning
        # is added to the suite's warning count.
        policy.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(loop)
@pytest_asyncio.fixture(scope="session", autouse=True)
async def _cleanup_module_level_async_engine() -> None:
    """Test-infrastructure cleanup for the module-level AsyncEngine.

    app.core.db.engine is created once at import time and shared by every DB
    test. When pytest-asyncio tears the session event loop down at session
    exit, finalizers of asyncpg pool objects still tied to that loop run
    during interpreter GC and raise a Windows access violation
    (asyncio/proactor_events.py __del__/__repr__).

    Yielding here keeps the fixture alive for the whole session; the engine is
    disposed afterwards, while the session event loop is still alive, so the
    pooled connections are detached before loop/GC shutdown.
    """
    yield

    from app.core.db import engine

    await engine.dispose()


