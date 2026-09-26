"""
Unit tests for RCAService._on_ai_rca_started with mocked repositories.

These tests run with NO database — they exercise the handler logic against
AsyncMock repos, which makes them cheap, fast insurance against regressions
like "someone adds `async` back" to `_persist_rca_results` or drops the
flush/commit pair.

Locked-in behaviours:
  - A successful RCA flushes AND commits the staged incident row
    (a flush alone only emits the UPDATE inside the open transaction; without
    commit the write rolls back when the session closes).
  - A successful RCA publishes RCA_COMPLETED.
  - An analyser failure raises through to the handler's except block (logged,
    no publish), and with a session_factory the open session is rolled back
    on exit — so a half-dirty transaction can never leak into the next
    incident's handler.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.incident import Incident
from app.services.events.event_bus import EventBus, EventType, DomainEvent
from app.services.rca_service import RCAService, RootCauseResult

pytestmark = pytest.mark.asyncio


async def _wait_for_capture(
    captured: list[DomainEvent],
    timeout: float = 2.0,
) -> DomainEvent | None:
    """Poll until the captured list is non-empty or we time out.

    ``EventBus.publish()`` only enqueues; the background worker (started via
    ``bus.start()``) dispatches to subscribers. Polling here lets us wait for
    that dispatch without coupling the test to worker internals.
    """
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    while datetime.now(timezone.utc).timestamp() < deadline:
        if captured:
            return captured[0]
        await asyncio.sleep(0.05)
    return None


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        title="Unit-test incident",
        severity="critical",
        status="open",
        alert_count=1,
        opened_at=None,  # not needed by the mocked path
        last_alert_at=None,
    )


def _make_event(incident_id: str, tenant_id: uuid.UUID) -> DomainEvent:
    return DomainEvent(
        event_type=EventType.AI_RCA_STARTED,
        tenant_id=tenant_id,
        correlation_id=str(uuid.uuid4()),
        payload={
            "ticket_id": str(uuid.uuid4()),
            "incident_id": incident_id,
        },
    )


def _make_session() -> MagicMock:
    """A MagicMock session whose flush/commit/rollback are awaitable.

    Plain MagicMock methods are NOT awaitable — the handler awaits
    ``db.flush()`` / ``db.commit()``, so we must back those attributes with
    AsyncMock. ``add`` is called synchronously (SQLAlchemy's async session
    ``add`` is sync), so it stays a plain MagicMock.
    """
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.add = MagicMock()
    return session


class _FakeAsyncContextManager:
    """Minimal async context manager yielding a MagicMock session."""

    def __init__(self, session: MagicMock):
        self._session = session

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *exc_info) -> None:
        # Record whether the session's transaction was rolled back on exit
        self._session._exited_with_exception = exc_info[0] is not None


def _make_session_factory(session: MagicMock):
    def _factory() -> _FakeAsyncContextManager:
        return _FakeAsyncContextManager(session)
    return _factory


def _make_repos(session: MagicMock, incident: Incident):
    incident_repo = MagicMock()
    incident_repo.db = session
    incident_repo.get = AsyncMock(return_value=incident)

    alert_repo = MagicMock()
    alert_repo.db = session
    alert_repo.list = AsyncMock(return_value=[])

    return alert_repo, incident_repo


def _make_service(
    *,
    alert_repo: MagicMock | None = None,
    incident_repo: MagicMock | None = None,
    session_factory=None,
    result: RootCauseResult | None = None,
    analyser_side_effect: Exception | None = None,
) -> RCAService:
    analyser = AsyncMock()
    if result is not None:
        analyser.analyse.return_value = result
    if analyser_side_effect is not None:
        analyser.analyse.side_effect = analyser_side_effect

    bus = EventBus()
    if session_factory is not None:
        service = RCAService(
            event_bus=bus,
            analyser=analyser,
            session_factory=session_factory,
        )
    else:
        service = RCAService(
            event_bus=bus,
            alert_repo=alert_repo,
            incident_repo=incident_repo,
            analyser=analyser,
        )
    return service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRCAServiceUnit:
    async def test_success_flushes_and_commits(self):
        """A successful RCA must flush AND commit the staged incident row."""
        incident = _make_incident()
        session = _make_session()
        alert_repo, incident_repo = _make_repos(session, incident)
        result = RootCauseResult(
            root_cause_summary="Root cause: Device abc.",
            supporting_evidence=[],
            recommended_sop_id=uuid.uuid4(),
        )

        service = _make_service(
            alert_repo=alert_repo,
            incident_repo=incident_repo,
            result=result,
        )
        event = _make_event(str(incident.id), incident.tenant_id)

        # Subscribe a spy BEFORE the handler runs (publish is synchronous
        # when calling the handler directly).
        published: list[DomainEvent] = []
        service.event_bus.subscribe(EventType.RCA_COMPLETED)(
            lambda ev: published.append(ev) or None  # type: ignore[return-value]
        )

        # Start the bus worker so queued events actually reach subscribers.
        await service.event_bus.start()
        try:
            await service._on_ai_rca_started(event)

            # The staged incident row must have been both flushed and committed —
            # flush alone leaves the UPDATE inside an uncommitted transaction
            # that rolls back when the session closes.
            session.flush.assert_awaited_once()
            session.commit.assert_awaited_once()

            # The ORM object was mutated before the flush.
            assert incident.root_cause_summary == result.root_cause_summary
            assert incident.recommended_sop == str(result.recommended_sop_id)

            # RCA_COMPLETED was published with the right payload. The handler
            # enqueues it; the worker dispatches to our spy asynchronously.
            completed = await _wait_for_capture(published)
            assert completed is not None, "RCA_COMPLETED was not dispatched"
            assert completed.payload["incident_id"] == str(incident.id)
            assert completed.payload["root_cause_summary"] == result.root_cause_summary
        finally:
            await service.event_bus.stop()

    async def test_success_with_session_factory_uses_fresh_session(self):
        """When a session_factory is injected, the handler opens a fresh
        session per event and commits through it."""
        incident = _make_incident()
        session = _make_session()
        result = RootCauseResult(
            root_cause_summary="Root cause summary.",
            supporting_evidence=[],
        )

        service = _make_service(
            session_factory=_make_session_factory(session),
            result=result,
        )
        event = _make_event(str(incident.id), incident.tenant_id)

        # Mock the fresh-session repos created inside the handler.
        incident_repo = MagicMock()
        incident_repo.db = session
        incident_repo.get = AsyncMock(return_value=incident)
        alert_repo = MagicMock()
        alert_repo.db = session
        alert_repo.list = AsyncMock(return_value=[])

        # Patch the repo constructors to return our mocks.
        import app.services.rca_service as rca_module
        rca_module.IncidentRepository = MagicMock(return_value=incident_repo)
        rca_module.AlertRepository = MagicMock(return_value=alert_repo)

        try:
            await service._on_ai_rca_started(event)

            session.flush.assert_awaited_once()
            session.commit.assert_awaited_once()
        finally:
            # Restore the real constructors so other tests are unaffected.
            import app.repositories.incident_repository as inc_repo_module
            import app.repositories.alert_repository as alert_repo_module
            rca_module.IncidentRepository = inc_repo_module.IncidentRepository
            rca_module.AlertRepository = alert_repo_module.AlertRepository

    async def test_analyser_failure_rolls_back_session_and_no_publish(self, caplog):
        """If the analyser raises, the handler:
          - logs 'RCA failed'
          - does NOT publish RCA_COMPLETED
          - with a session_factory, the open session's transaction is rolled
            back on exit (never leaks into the next incident's handler)."""
        incident = _make_incident()
        session = _make_session()

        service = _make_service(
            session_factory=_make_session_factory(session),
            analyser_side_effect=RuntimeError("LLM unreachable"),
        )
        event = _make_event(str(incident.id), incident.tenant_id)

        # Patch the fresh-session repo constructors like the success test.
        incident_repo = MagicMock()
        incident_repo.db = session
        incident_repo.get = AsyncMock(return_value=incident)
        alert_repo = MagicMock()
        alert_repo.db = session
        alert_repo.list = AsyncMock(return_value=[])

        import app.services.rca_service as rca_module
        rca_module.IncidentRepository = MagicMock(return_value=incident_repo)
        rca_module.AlertRepository = MagicMock(return_value=alert_repo)

        published: list[DomainEvent] = []
        service.event_bus.subscribe(EventType.RCA_COMPLETED)(
            lambda ev: published.append(ev) or None  # type: ignore[return-value]
        )

        try:
            await service._on_ai_rca_started(event)
        finally:
            import app.repositories.incident_repository as inc_repo_module
            import app.repositories.alert_repository as alert_repo_module
            rca_module.IncidentRepository = inc_repo_module.IncidentRepository
            rca_module.AlertRepository = alert_repo_module.AlertRepository

        # No publish on failure.
        assert published == []

        # No flush/commit attempted after the analyser raised.
        session.flush.assert_not_awaited()
        session.commit.assert_not_awaited()

        # The error was logged with the incident id.
        assert any(
            "RCA failed" in rec.message and str(incident.id) in rec.message
            for rec in caplog.records
        )

        # The session context manager recorded an exception on exit — the
        # async-with exit rolls back the half-open transaction.
        assert session._exited_with_exception is True

    async def test_repo_failure_logs_and_no_publish(self, caplog):
        """If the incident lookup raises, the handler catches it and the
        event bus stays healthy."""
        incident = _make_incident()
        session = _make_session()

        incident_repo = MagicMock()
        incident_repo.db = session
        incident_repo.get = AsyncMock(side_effect=RuntimeError("db down"))

        alert_repo = MagicMock()
        alert_repo.db = session
        alert_repo.list = AsyncMock(return_value=[])

        service = _make_service(
            alert_repo=alert_repo,
            incident_repo=incident_repo,
            result=RootCauseResult(root_cause_summary="n/a", supporting_evidence=[]),
        )
        event = _make_event(str(incident.id), incident.tenant_id)

        published: list[DomainEvent] = []
        service.event_bus.subscribe(EventType.RCA_COMPLETED)(
            lambda ev: published.append(ev) or None  # type: ignore[return-value]
        )

        await service._on_ai_rca_started(event)

        assert published == []
        session.flush.assert_not_awaited()
        session.commit.assert_not_awaited()
        assert any(
            "RCA failed" in rec.message for rec in caplog.records
        )

    async def test_construction_requires_repos_or_factory(self):
        """RCAService must be constructed with either a session_factory or
        both repos — prevents accidental mis-wiring."""
        bus = EventBus()
        with pytest.raises(ValueError):
            RCAService(event_bus=bus)  # type: ignore[call-arg]

        with pytest.raises(ValueError):
            RCAService(
                event_bus=bus,
                alert_repo=MagicMock(),  # missing incident_repo
            )
