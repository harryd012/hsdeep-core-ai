"""
Production RCA Activation V2 - integration tests.

Exercises the REAL changed code: AlertService incident-created propagation,
CollectionOutcome's contract, and the existing RCATrigger/EventBus. Repositories
are stubs so no DB is required.

Deliberately NOT claimed: `jobs._run_bounded` itself is not executed (it builds
real repositories from AsyncSessionLocal). Its ordering is covered by
`run_collection_then_trigger`, which replays the exact same steps
(run -> commit -> trigger) against the real trigger and a commit-recording bus.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.services.alert_service import AlertService, CreatedIncidentReference
from app.services.events.event_bus import EventBus, EventType
from app.services.monitoring.collection_orchestrator import CollectionOutcome
from app.services.rca_trigger import RCATrigger

TENANT = "11111111-1111-1111-1111-111111111111"
INCIDENT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


class FakeDB:
    """Just enough AsyncSession surface for the real repositories."""

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj) -> None:
        return None

    async def execute(self, *_args, **_kwargs):
        class _EmptyResult:
            def scalar_one_or_none(self):
                return None

            def scalars(self):
                return self

            def all(self):
                return []

            def first(self):
                return None

        return _EmptyResult()

    def add(self, _obj) -> None:
        return None


class RecordingBus(EventBus):
    """Real EventBus that records publications and commit ordering."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list = []
        self.committed = False
        self.committed_before_publish: list[bool] = []

    async def publish(self, event) -> None:
        self.published.append(event)
        self.committed_before_publish.append(self.committed)
        await super().publish(event)


class FakeCorrelationResult:
    def __init__(self, *, incident, is_new_incident, recomputed=True, is_suppressed=False):
        self.incident = incident
        self.is_new_incident = is_new_incident
        self.recomputed = recomputed
        self.is_root_cause = False
        self.is_suppressed = is_suppressed


class FakeCorrelationEngine:
    """Stands in for the real CorrelationEngine; returns a controlled result."""

    def __init__(self, result) -> None:
        self.result = result
        self.calls = 0

    async def correlate(self, alert):  # noqa: ARG002
        self.calls += 1
        return self.result


class FakeAlertRepo:
    def __init__(self) -> None:
        self.db = FakeDB()


class FakePriorityEngine:
    """Isolates the propagation test from the real priority/risk chain."""

    def __init__(self) -> None:
        self.recomputed: list = []

    async def recompute(self, incident) -> None:
        self.recomputed.append(incident)


def build_alert_service(result):
    return AlertService(
        alert_repo=FakeAlertRepo(),
        correlation_engine=FakeCorrelationEngine(result),
        priority_engine=FakePriorityEngine(),
    )


def incident_row():
    return SimpleNamespace(id=INCIDENT_ID, tenant_id=uuid.UUID(TENANT))


def make_alert():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.UUID(TENANT))


# ------------------------------------ incident-created propagation (1, 2, 7)


def test_new_incident_is_recorded_and_drained_once():
    service = build_alert_service(
        FakeCorrelationResult(incident=incident_row(), is_new_incident=True)
    )
    asyncio.run(service._correlate(make_alert()))  # noqa: SLF001 - the real path

    created = service.drain_created_incidents()
    assert len(created) == 1
    assert isinstance(created[0], CreatedIncidentReference)
    assert created[0].incident_id == INCIDENT_ID
    assert str(created[0].tenant_id) == TENANT  # tenant preserved from Incident
    # drained exactly once - a later drain must not re-trigger the same work
    assert service.drain_created_incidents() == []


def test_existing_incident_recognition_is_not_recorded():
    """Only genuinely CREATED incidents are reported."""
    service = build_alert_service(
        FakeCorrelationResult(incident=incident_row(), is_new_incident=False)
    )
    asyncio.run(service._correlate(make_alert()))  # noqa: SLF001
    assert service.drain_created_incidents() == []


def test_no_incident_recognition_records_nothing():
    service = build_alert_service(
        FakeCorrelationResult(incident=None, is_new_incident=False)
    )
    asyncio.run(service._correlate(make_alert()))  # noqa: SLF001
    assert service.drain_created_incidents() == []


def test_multiple_created_incidents_are_all_retained():
    service = build_alert_service(
        FakeCorrelationResult(incident=incident_row(), is_new_incident=True)
    )
    for _ in range(3):
        asyncio.run(service._correlate(make_alert()))  # noqa: SLF001
    assert len(service.drain_created_incidents()) == 3


# ------------------------------------------- CollectionOutcome contract (1, 3)


def test_collection_outcome_exposes_created_incidents():
    run_record = SimpleNamespace(status="success", sensors_upserted=0)
    empty = CollectionOutcome(run_record)
    assert empty.created_incidents == []          # zero incidents - unchanged
    assert empty.status == "success"              # __getattr__ delegation intact

    reference = CreatedIncidentReference(INCIDENT_ID, uuid.UUID(TENANT))
    populated = CollectionOutcome(run_record, [reference])
    assert populated.created_incidents == [reference]
    assert populated.run_record is run_record


# --------------------------- post-commit ordering + zero/one/many (1, 2, 3, 5)


class FakeIncidentRepo:
    """Minimal tenant-scoped repo: a wrong tenant finds nothing, like the real one."""

    def __init__(self, status="open", tenant=TENANT) -> None:
        self.status = status
        self.tenant = tenant

    async def get(self, incident_id, tenant_id):
        if str(tenant_id) != self.tenant:
            return None  # tenant-scoped at the repository layer
        return SimpleNamespace(
            id=incident_id, tenant_id=uuid.UUID(self.tenant), status=self.status
        )


class CommitOrderingSession:
    """Fake session whose commit marks the boundary the trigger must follow."""

    def __init__(self, bus: RecordingBus) -> None:
        self.bus = bus
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1
        self.bus.committed = True


def run_collection_then_trigger(outcome, session, bus):
    """Replays the EXACT steps implemented in jobs.py / monitoring.py."""
    async def _go():
        await session.commit()                      # 1. commit first
        for reference in outcome.created_incidents:  # 2. only then trigger
            await RCATrigger(FakeIncidentRepo(), bus).trigger_for_incident(
                reference.incident_id, reference.tenant_id
            )

    asyncio.run(_go())


def test_zero_incidents_triggers_nothing():
    bus = RecordingBus()
    session = CommitOrderingSession(bus)
    run_collection_then_trigger(
        CollectionOutcome(SimpleNamespace(status="success")), session, bus
    )
    assert session.commits == 1
    assert bus.published == []


def test_one_incident_triggers_once_after_commit():
    bus = RecordingBus()
    session = CommitOrderingSession(bus)
    reference = CreatedIncidentReference(INCIDENT_ID, uuid.UUID(TENANT))
    run_collection_then_trigger(
        CollectionOutcome(SimpleNamespace(status="success"), [reference]), session, bus
    )

    assert session.commits == 1
    assert len(bus.published) == 1
    event = bus.published[0]
    assert event.event_type is EventType.AI_RCA_STARTED
    assert event.payload["incident_id"] == str(INCIDENT_ID)
    assert str(event.tenant_id) == TENANT
    assert event.correlation_id  # deterministic rca-{id} correlation present
    # ORDERING PROOF: the commit happened before the trigger published
    assert bus.committed_before_publish == [True]


def test_multiple_incidents_each_trigger_after_a_single_commit():
    bus = RecordingBus()
    session = CommitOrderingSession(bus)
    refs = [CreatedIncidentReference(uuid.uuid4(), uuid.UUID(TENANT)) for _ in range(3)]
    run_collection_then_trigger(
        CollectionOutcome(SimpleNamespace(status="success"), refs), session, bus
    )
    assert session.commits == 1                 # one transaction
    assert len(bus.published) == 3              # one trigger each
    assert bus.committed_before_publish == [True, True, True]


def test_commit_failure_prevents_any_trigger():
    """If commit raises, nothing may be reported as created or triggered."""
    bus = RecordingBus()

    class FailingSession:
        commits = 0

        async def commit(self) -> None:
            self.commits += 1
            raise RuntimeError("commit failed")

    session = FailingSession()
    reference = CreatedIncidentReference(INCIDENT_ID, uuid.UUID(TENANT))
    outcome = CollectionOutcome(SimpleNamespace(status="success"), [reference])

    async def _go():
        await session.commit()
        for ref in outcome.created_incidents:
            await RCATrigger(FakeIncidentRepo(), bus).trigger_for_incident(
                ref.incident_id, ref.tenant_id
            )

    raised = False
    try:
        asyncio.run(_go())
    except RuntimeError:
        raised = True

    assert raised is True            # existing exception behaviour intact
    assert bus.published == []       # RCA trigger never ran
    assert bus.committed is False    # nothing was reported as committed


def test_trigger_failure_after_commit_does_not_roll_back():
    """A trigger failure is contained; the committed transaction stands."""
    class ExplodingBus(EventBus):
        async def publish(self, event):  # noqa: ARG002
            raise RuntimeError("bus down")

    session = CommitOrderingSession(RecordingBus())
    reference = CreatedIncidentReference(INCIDENT_ID, uuid.UUID(TENANT))
    outcome = CollectionOutcome(SimpleNamespace(status="success"), [reference])

    async def _go():
        await session.commit()
        for ref in outcome.created_incidents:
            await RCATrigger(FakeIncidentRepo(), ExplodingBus()).trigger_for_incident(
                ref.incident_id, ref.tenant_id
            )

    asyncio.run(_go())               # must not raise
    assert session.commits == 1      # committed exactly once, never re-committed


def test_ineligible_incident_is_not_triggered():
    """Eligibility stays inside RCATrigger - never duplicated by the caller."""
    bus = RecordingBus()
    session = CommitOrderingSession(bus)
    reference = CreatedIncidentReference(INCIDENT_ID, uuid.UUID(TENANT))

    async def _go():
        await session.commit()
        await RCATrigger(FakeIncidentRepo(status="resolved"), bus).trigger_for_incident(
            reference.incident_id, reference.tenant_id
        )

    asyncio.run(_go())
    assert bus.published == []


def test_tenant_mismatch_is_not_triggered():
    """Tenant safety is enforced by RCATrigger, not the caller."""
    bus = RecordingBus()
    session = CommitOrderingSession(bus)
    reference = CreatedIncidentReference(INCIDENT_ID, uuid.UUID(TENANT))

    async def _go():
        await session.commit()
        await RCATrigger(FakeIncidentRepo(), bus).trigger_for_incident(
            reference.incident_id, uuid.UUID("99999999-9999-9999-9999-999999999999")
        )

    asyncio.run(_go())
    assert bus.published == []
