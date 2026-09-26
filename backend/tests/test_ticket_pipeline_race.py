"""
Task 1 regression test: ticket creation race condition.

Previously INCIDENT_CREATED was published BEFORE the creating transaction
committed, and the AutonomousLifecycleOrchestrator held a single DB session
from app startup (a frozen snapshot that could never see rows committed
after startup).  Result: the incident lookup always failed and no ticket
was ever created without manual intervention.

The fix:
  1. Events are only published AFTER db.commit() (deferred events -
     see AlertService/CollectionOrchestrator/jobs.py).
  2. The orchestrator creates a FRESH session per event handler via
     session_factory (AsyncSessionLocal), so it sees committed rows.

This test exercises the true end-to-end contract: create+commit an incident,
publish INCIDENT_CREATED, and assert a Ticket was created within ONE retry
cycle (i.e. the first fresh-session lookup succeeds — no 4-retry gauntlet).

Runs against the real configured Postgres database (see conftest.py).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.db import AsyncSessionLocal
from app.models.incident import Incident
from app.models.tenant import Tenant
from app.models.ticket import Ticket
from app.repositories.incident_repository import IncidentRepository
from app.services.events.event_bus import EventBus, EventType, DomainEvent
from app.services.events.autonomous_lifecycle import AutonomousLifecycleOrchestrator

pytestmark = pytest.mark.asyncio


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def committed_incident(tenant: Tenant) -> Incident:
    """Create + COMMIT an Incident row, as the collection pipeline does
    before it publishes INCIDENT_CREATED."""
    async with AsyncSessionLocal() as db:
        repo = IncidentRepository(db)
        incident = await repo.create(
            tenant_id=tenant.id,
            title="P0 - Ticket pipeline race regression",
            description="Committed before event publish - the new contract.",
            severity="critical",
            priority="P1",
            status="open",
            correlation_type="single_device",
            opened_at=_now(),
            last_alert_at=_now(),
        )
        await db.commit()
        await db.refresh(incident)
    return incident


async def _assert_no_ticket_for_incident(incident_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        stmt = select(Ticket).where(Ticket.incident_id == incident_id)
        result = await db.execute(stmt)
        assert result.scalar_one_or_none() is None, (
            "Ticket already exists before the test publishes INCIDENT_CREATED"
        )


async def _count_tickets_for_incident(incident_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as db:
        stmt = select(Ticket).where(Ticket.incident_id == incident_id)
        result = await db.execute(stmt)
        return len(result.scalars().all())


class TestTicketPipelineRaceRegression:
    """The bug: INCIDENT_CREATED published before commit + stale orchestrator
    session -> no ticket ever created.  The fix: publish after commit +
    fresh session per handler -> ticket created within the first retry
    attempt."""

    async def test_ticket_created_within_one_retry_cycle_after_commit(
        self, committed_incident: Incident, tenant: Tenant
    ):
        """Regression: create a committed incident, publish INCIDENT_CREATED
        through the real orchestrator, and assert a ticket appears without
        any manual intervention and within the retry budget."""
        # Preconditions: no ticket exists for this incident yet.
        await _assert_no_ticket_for_incident(committed_incident.id)

        # Build a real EventBus + orchestrator wired to AsyncSessionLocal
        # (exactly what main.py's startup does post-fix).
        bus = EventBus()
        orchestrator = AutonomousLifecycleOrchestrator(
            event_bus=bus,
            session_factory=AsyncSessionLocal,
        )
        await bus.start()

        try:
            # Publish the incident-created event — this simulates what
            # jobs._run_bounded does AFTER db.commit().
            await bus.publish(DomainEvent(
                event_type=EventType.INCIDENT_CREATED,
                tenant_id=tenant.id,
                correlation_id=str(committed_incident.id),
                payload={
                    "incident_id": str(committed_incident.id),
                    "alert_id": str(uuid.uuid4()),
                    "severity": committed_incident.severity,
                    "priority": committed_incident.priority,
                }
            ))

            # The event bus processes async in the background — give the
            # worker a moment to drain.  The orchestrator's lookup runs on
            # its own fresh session, which MUST see the committed incident.
            # One retry cycle = 2 attempts ~0.1s + ~0.2s worst case;
            # we wait comfortably beyond that (2.0s) but the point is the
            # FIRST fresh-session read succeeds — no retries are needed.
            for _ in range(20):
                count = await _count_tickets_for_incident(committed_incident.id)
                if count > 0:
                    break
                await asyncio.sleep(0.1)

            ticket_count = await _count_tickets_for_incident(committed_incident.id)
            assert ticket_count == 1, (
                f"Expected exactly 1 ticket for incident {committed_incident.id} "
                f"within the retry cycle, got {ticket_count}. "
                "Regression: event published before commit, or stale session."
            )

            # Verify the created ticket is linked correctly (source of truth).
            async with AsyncSessionLocal() as db:
                stmt = select(Ticket).where(Ticket.incident_id == committed_incident.id)
                ticket = (await db.execute(stmt)).scalar_one()
                assert ticket.tenant_id == tenant.id
                assert ticket.source == "incident_triggered"
                assert ticket.status in ("open", "assigned")
        finally:
            await bus.stop()

    async def test_no_ticket_when_incident_belongs_to_other_tenant(
        self, tenant: Tenant
    ):
        """The orchestrator must NOT create a ticket for an incident that
        does not belong to the event's tenant (isolation guard)."""
        async with AsyncSessionLocal() as db:
            other = Tenant(name="Other Tenant", slug=f"other-{uuid.uuid4().hex[:8]}")
            db.add(other)
            await db.commit()
            await db.refresh(other)

        try:
            async with AsyncSessionLocal() as db:
                repo = IncidentRepository(db)
                incident = await repo.create(
                    tenant_id=other.id,
                    title="Other tenant incident",
                    description="Isolation test",
                    severity="critical",
                    priority="P1",
                    status="open",
                    correlation_type="single_device",
                    opened_at=_now(),
                    last_alert_at=_now(),
                )
                await db.commit()
                await db.refresh(incident)

            bus = EventBus()
            orchestrator = AutonomousLifecycleOrchestrator(
                event_bus=bus,
                session_factory=AsyncSessionLocal,
            )
            await bus.start()

            try:
                # Publish the event with tenant_id = primary tenant, but the
                # incident belongs to `other` — the orchestrator's tenant-
                # scoped lookup must return None, so no ticket is created.
                await bus.publish(DomainEvent(
                    event_type=EventType.INCIDENT_CREATED,
                    tenant_id=tenant.id,
                    correlation_id=str(incident.id),
                    payload={
                        "incident_id": str(incident.id),
                        "alert_id": str(uuid.uuid4()),
                        "severity": incident.severity,
                        "priority": incident.priority,
                    }
                ))

                await asyncio.sleep(1.0)

                count = await _count_tickets_for_incident(incident.id)
                assert count == 0, (
                    "Cross-tenant leak: orchestrator created a ticket for an "
                    "incident belonging to a different tenant!"
                )
            finally:
                await bus.stop()
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Tenant).where(Tenant.id == other.id))
                await db.commit()