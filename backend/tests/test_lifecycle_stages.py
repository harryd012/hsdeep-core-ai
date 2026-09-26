"""
Lifecycle stage tests — Stage 1: RCA Listener (AI_RCA_STARTED → RCA_COMPLETED).

Covers:
  - Happy path: AI_RCA_STARTED produces RCA_COMPLETED with correct payload
    and persists root_cause_summary / supporting_evidence / recommended_sop
    on the Incident row.
  - Missing incident_id in event → early return, no publish, no error.
  - Incident not found → logged error, no publish.
  - Analyser exception → logged error, no publish, no crash (event bus stays healthy).

Follows the project's real-Postgres convention from conftest.py (no SQLite).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.db import AsyncSessionLocal
from app.models.alert import Alert
from app.models.incident import Incident
from app.repositories.incident_repository import IncidentRepository
from app.services.events.event_bus import EventBus, EventType, DomainEvent
from app.services.rca_service import RCAService, RootCauseResult

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_analyser(result: RootCauseResult) -> AsyncMock:
    """Return an analyser mock that always produces the given result."""
    mock = AsyncMock()
    mock.analyse.return_value = result
    return mock


def _make_event_bus() -> EventBus:
    return EventBus()


def _capture(bus: EventBus, event_type: EventType) -> list[DomainEvent]:
    """Attach a capturing spy to the bus. Call BEFORE publishing, otherwise
    events that fire between publish() and the first poll are missed."""
    captured: list[DomainEvent] = []
    bus.subscribe(event_type)(captured.append)  # type: ignore[arg-type]
    return captured


async def _wait_for_capture(
    captured: list[DomainEvent], timeout: float = 2.0
) -> DomainEvent | None:
    """Poll an already-attached capture list until it is non-empty."""
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    while datetime.now(timezone.utc).timestamp() < deadline:
        await asyncio.sleep(0.05)
        if captured:
            return captured[0]
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def incident_row(tenant):
    """A committed Incident row scoped to `tenant`, deleted on teardown."""
    async with AsyncSessionLocal() as db:
        incident = Incident(
            tenant_id=tenant.id,
            title="Test incident for RCA",
            description="Stage 1 test incident",
            severity="critical",
            status="open",
            alert_count=2,
            opened_at=datetime.now(timezone.utc),
            last_alert_at=datetime.now(timezone.utc),
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
    yield incident
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Incident).where(Incident.id == incident.id))
        await db.commit()


@pytest_asyncio.fixture
async def alert_rows(tenant, incident_row):
    """Two committed Alert rows linked to `incident_row`."""
    async with AsyncSessionLocal() as db:
        a1 = Alert(
            tenant_id=tenant.id,
            incident_id=incident_row.id,
            site_id=incident_row.site_id,
            device_id=incident_row.device_id,
            title="Primary alert — root cause candidate",
            severity="critical",
            status="open",
            is_root_cause_candidate=True,
            triggered_at=datetime.now(timezone.utc),
            last_occurred_at=datetime.now(timezone.utc),
            occurrence_count=1,
        )
        a2 = Alert(
            tenant_id=tenant.id,
            incident_id=incident_row.id,
            site_id=incident_row.site_id,
            device_id=incident_row.device_id,
            title="Correlated secondary alert",
            severity="warning",
            status="open",
            is_root_cause_candidate=False,
            triggered_at=datetime.now(timezone.utc),
            last_occurred_at=datetime.now(timezone.utc),
            occurrence_count=1,
        )
        db.add_all([a1, a2])
        await db.commit()
        await db.refresh(a1)
        await db.refresh(a2)
    yield [a1, a2]
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Alert).where(Alert.incident_id == incident_row.id))
        await db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRCAListener:
    """Stage 1: AI_RCA_STARTED → RCA_COMPLETED."""

    async def test_happy_path_publishes_rca_completed(
        self, tenant, incident_row, alert_rows
    ):
        """AI_RCA_STARTED → RCA_COMPLETED with correct payload fields,
        and the Incident row is updated with RCA results."""
        fake_result = RootCauseResult(
            root_cause_summary="Root cause: Device abc123, category 'network'.",
            supporting_evidence=[
                {"alert_id": str(alert_rows[0].id), "reason": "root-cause candidate"},
                {"alert_id": str(alert_rows[1].id), "reason": "correlated alert"},
            ],
            recommended_sop_id=uuid.uuid4(),
            recommended_sop_name="SOP-001 Device Outage Response",
            requires_approval=False,
        )
        fake_analyser = _make_fake_analyser(fake_result)

        bus = _make_event_bus()
        await bus.start()

        try:
            rca_service = RCAService(
                event_bus=bus,
                analyser=fake_analyser,
                session_factory=AsyncSessionLocal,
            )
            rca_service.register()
            completed_events = _capture(bus, EventType.RCA_COMPLETED)

            event = DomainEvent(
                event_type=EventType.AI_RCA_STARTED,
                tenant_id=tenant.id,
                correlation_id=str(uuid.uuid4()),
                payload={
                    "ticket_id": str(uuid.uuid4()),
                    "incident_id": str(incident_row.id),
                },
            )
            await bus.publish(event)
            await bus.publish_and_wait(event)

            # Give the background worker a moment
            await asyncio.sleep(0.2)

            # --- Assert publish ---
            completed = await _wait_for_capture(completed_events, timeout=3.0)
            assert completed is not None, "RCA_COMPLETED event was not published"
            assert completed.event_type == EventType.RCA_COMPLETED
            assert completed.tenant_id == tenant.id
            assert completed.payload["ticket_id"] == event.payload["ticket_id"]
            assert completed.payload["incident_id"] == str(incident_row.id)
            assert completed.payload["root_cause_summary"] == fake_result.root_cause_summary
            assert completed.payload["recommended_sop_name"] == fake_result.recommended_sop_name
            assert completed.payload["requires_approval"] is False
            assert completed.payload["supporting_evidence"] == fake_result.supporting_evidence

            # --- Assert persistence ---
            async with AsyncSessionLocal() as db:
                incident_repo = IncidentRepository(db)
                refreshed = await incident_repo.get(incident_row.id, tenant.id)
                assert refreshed is not None
                assert refreshed.root_cause_summary == fake_result.root_cause_summary
                assert refreshed.supporting_evidence["evidence"] == fake_result.supporting_evidence
                assert refreshed.recommended_sop == str(fake_result.recommended_sop_id)

        finally:
            await bus.stop()

    async def test_missing_incident_id_logs_warning_and_returns(
        self, tenant, incident_row, alert_rows
    ):
        """Event with no incident_id → warning log, no publish, no crash."""
        fake_analyser = _make_fake_analyser(
            RootCauseResult(root_cause_summary="n/a", supporting_evidence=[])
        )
        bus = _make_event_bus()
        await bus.start()

        try:
            rca_service = RCAService(
                event_bus=bus,
                analyser=fake_analyser,
                session_factory=AsyncSessionLocal,
            )
            rca_service.register()
            completed_events = _capture(bus, EventType.RCA_COMPLETED)

            event = DomainEvent(
                event_type=EventType.AI_RCA_STARTED,
                tenant_id=tenant.id,
                correlation_id=str(uuid.uuid4()),
                payload={"ticket_id": str(uuid.uuid4())},  # no incident_id
            )
            await bus.publish(event)
            await bus.publish_and_wait(event)

            await asyncio.sleep(0.2)

            # No RCA_COMPLETED should have been published
            completed = await _wait_for_capture(completed_events, timeout=1.0)
            assert completed is None
            fake_analyser.analyse.assert_not_called()
        finally:
            await bus.stop()

    async def test_incident_not_found_logs_error_and_returns(
        self, tenant, alert_rows
    ):
        """Event references a non-existent incident → error log, no publish."""
        fake_analyser = _make_fake_analyser(
            RootCauseResult(root_cause_summary="n/a", supporting_evidence=[])
        )
        bus = _make_event_bus()
        await bus.start()

        try:
            rca_service = RCAService(
                event_bus=bus,
                analyser=fake_analyser,
                session_factory=AsyncSessionLocal,
            )
            rca_service.register()
            completed_events = _capture(bus, EventType.RCA_COMPLETED)

            bogus_id = str(uuid.uuid4())
            event = DomainEvent(
                event_type=EventType.AI_RCA_STARTED,
                tenant_id=tenant.id,
                correlation_id=str(uuid.uuid4()),
                payload={
                    "ticket_id": str(uuid.uuid4()),
                    "incident_id": bogus_id,
                },
            )
            await bus.publish(event)
            await bus.publish_and_wait(event)

            await asyncio.sleep(0.2)

            completed = await _wait_for_capture(completed_events, timeout=1.0)
            assert completed is None
            fake_analyser.analyse.assert_not_called()
        finally:
            await bus.stop()

    async def test_analyser_exception_does_not_crash_bus(
        self, tenant, incident_row, alert_rows, caplog
    ):
        """If the analyser raises, the handler catches it, logs, and does not
        publish RCA_COMPLETED — the event bus stays healthy."""
        fake_analyser = AsyncMock()
        fake_analyser.analyse.side_effect = RuntimeError("LLM unreachable")

        bus = _make_event_bus()
        await bus.start()

        try:
            rca_service = RCAService(
                event_bus=bus,
                analyser=fake_analyser,
                session_factory=AsyncSessionLocal,
            )
            rca_service.register()
            completed_events = _capture(bus, EventType.RCA_COMPLETED)

            event = DomainEvent(
                event_type=EventType.AI_RCA_STARTED,
                tenant_id=tenant.id,
                correlation_id=str(uuid.uuid4()),
                payload={
                    "ticket_id": str(uuid.uuid4()),
                    "incident_id": str(incident_row.id),
                },
            )
            await bus.publish(event)
            await bus.publish_and_wait(event)

            await asyncio.sleep(0.2)

            completed = await _wait_for_capture(completed_events, timeout=1.0)
            assert completed is None

            # Verify the error was logged
            assert any(
                "RCA failed" in rec.message and str(incident_row.id) in rec.message
                for rec in caplog.records
            )
        finally:
            await bus.stop()

    async def test_rca_completed_payload_propagates_requires_approval(
        self, tenant, incident_row, alert_rows
    ):
        """requires_approval=True from the analyser flows through to
        RCA_COMPLETED so _on_rca_completed can decide auto_remediate."""
        fake_result = RootCauseResult(
            root_cause_summary="Needs human sign-off.",
            supporting_evidence=[],
            recommended_sop_id=uuid.uuid4(),
            recommended_sop_name="SOP-002 Gated Change",
            requires_approval=True,
        )
        fake_analyser = _make_fake_analyser(fake_result)
        bus = _make_event_bus()
        await bus.start()

        try:
            rca_service = RCAService(
                event_bus=bus,
                analyser=fake_analyser,
                session_factory=AsyncSessionLocal,
            )
            rca_service.register()
            completed_events = _capture(bus, EventType.RCA_COMPLETED)

            event = DomainEvent(
                event_type=EventType.AI_RCA_STARTED,
                tenant_id=tenant.id,
                correlation_id=str(uuid.uuid4()),
                payload={
                    "ticket_id": str(uuid.uuid4()),
                    "incident_id": str(incident_row.id),
                },
            )
            await bus.publish(event)
            await bus.publish_and_wait(event)

            await asyncio.sleep(0.2)

            completed = await _wait_for_capture(completed_events, timeout=3.0)
            assert completed is not None
            assert completed.payload["requires_approval"] is True
        finally:
            await bus.stop()
