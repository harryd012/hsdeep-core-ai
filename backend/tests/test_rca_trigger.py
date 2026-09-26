"""
Production RCA Trigger V1 tests.

Covers eligibility, trigger validation, event payload/metadata, tenant safety,
failure isolation and the INCIDENT_CREATED bridge. Real EventBus; repositories
are stubs so no DB is required. Async via asyncio.run.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.events.event_bus import DomainEvent, EventBus, EventType
from app.services.rca_trigger import (
    RCA_ELIGIBLE_STATUS,
    RCAIncidentCreatedBridge,
    RCATrigger,
    default_correlation_id,
    is_rca_eligible,
    trigger_rca_for_incident,
)

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
INCIDENT_ID = "33333333-3333-3333-3333-333333333333"
DEVICE_ID = "44444444-4444-4444-4444-444444444444"
TICKET_ID = "55555555-5555-5555-5555-555555555555"


class RecordingBus(EventBus):
    """Real EventBus that also records what was published."""

    def __init__(self) -> None:
        super().__init__()
        self.published: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.published.append(event)
        await super().publish(event)


def incident(*, status=RCA_ELIGIBLE_STATUS, tenant=TENANT, incident_id=INCIDENT_ID):
    return SimpleNamespace(
        id=incident_id,
        tenant_id=tenant,
        device_id=DEVICE_ID,
        site_id=None,
        priority="P2",
        status=status,
        root_cause_summary=None,
        supporting_evidence=None,
        recommended_sop=None,
    )


class FakeIncidentRepo:
    def __init__(self, rows, db=None) -> None:
        self.rows = {str(r.id): r for r in rows}
        self.db = db

    async def get(self, incident_id, tenant_id):
        row = self.rows.get(str(incident_id))
        if row is None or str(row.tenant_id) != str(tenant_id):
            return None
        return row


# ------------------------------------------------------ eligibility (1, 2, 5)


def test_eligible_incident_produces_ai_rca_started():
    bus = RecordingBus()
    ok = asyncio.run(
        RCATrigger(FakeIncidentRepo([incident()]), bus).trigger_for_incident(
            INCIDENT_ID, TENANT
        )
    )
    assert ok is True
    assert len(bus.published) == 1
    assert bus.published[0].event_type is EventType.AI_RCA_STARTED


def test_ineligible_and_terminal_incidents_do_not_trigger():
    for status in ("resolved", "closed", "archived", "investigating", "monitoring", ""):
        assert is_rca_eligible(incident(status=status)) is False, status
        bus = RecordingBus()
        ok = asyncio.run(
            RCATrigger(FakeIncidentRepo([incident(status=status)]), bus)
            .trigger_for_incident(INCIDENT_ID, TENANT)
        )
        assert ok is False
        assert bus.published == []


def test_only_open_is_eligible():
    assert is_rca_eligible(incident(status="open")) is True
    assert is_rca_eligible(None) is False


def test_missing_incident_does_not_publish():
    bus = RecordingBus()
    ok = asyncio.run(
        trigger_rca_for_incident(
            "99999999-9999-9999-9999-999999999999",
            TENANT,
            incident_repo=FakeIncidentRepo([incident()]),
            event_bus=bus,
        )
    )
    assert ok is False and bus.published == []


# ------------------------------------------------- payload + metadata (4-7)


def test_event_payload_and_metadata_are_correct():
    bus = RecordingBus()
    asyncio.run(
        trigger_rca_for_incident(
            INCIDENT_ID,
            TENANT,
            incident_repo=FakeIncidentRepo([incident()]),
            event_bus=bus,
            ticket_id=TICKET_ID,
        )
    )
    event = bus.published[0]
    assert event.payload["incident_id"] == INCIDENT_ID
    assert event.payload["ticket_id"] == TICKET_ID
    assert str(event.tenant_id) == TENANT
    assert event.correlation_id == default_correlation_id(INCIDENT_ID)


def test_ticket_id_is_none_when_unavailable():
    bus = RecordingBus()
    asyncio.run(
        trigger_rca_for_incident(
            INCIDENT_ID,
            TENANT,
            incident_repo=FakeIncidentRepo([incident()]),
            event_bus=bus,
        )
    )
    assert bus.published[0].payload["ticket_id"] is None


def test_explicit_correlation_id_is_preserved():
    bus = RecordingBus()
    asyncio.run(
        trigger_rca_for_incident(
            INCIDENT_ID,
            TENANT,
            incident_repo=FakeIncidentRepo([incident()]),
            event_bus=bus,
            correlation_id="lifecycle-abc",
        )
    )
    assert bus.published[0].correlation_id == "lifecycle-abc"


# ------------------------------------------------------------ tenant safety (8)


def test_cross_tenant_incident_is_rejected():
    bus = RecordingBus()
    ok = asyncio.run(
        RCATrigger(FakeIncidentRepo([incident()]), bus).trigger_for_incident(
            INCIDENT_ID, OTHER_TENANT
        )
    )
    assert ok is False
    assert bus.published == []  # no cross-tenant event ever emitted


# -------------------------------------------------------------- failure (13)


def test_publish_failure_does_not_raise():
    class ExplodingBus(EventBus):
        async def publish(self, event):  # noqa: ARG002
            raise RuntimeError("bus down")

    ok = asyncio.run(
        trigger_rca_for_incident(
            INCIDENT_ID,
            TENANT,
            incident_repo=FakeIncidentRepo([incident()]),
            event_bus=ExplodingBus(),
        )
    )
    assert ok is False


def test_repo_failure_does_not_raise():
    class ExplodingRepo:
        async def get(self, *_a, **_k):
            raise RuntimeError("db down")

    ok = asyncio.run(
        RCATrigger(ExplodingRepo(), RecordingBus()).trigger_for_incident(
            INCIDENT_ID, TENANT
        )
    )
    assert ok is False


# ---------------------------------------------------- bridge to RCALifecycle


class FakeDB:
    def __init__(self) -> None:
        self.commits = 0

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

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


class FakeAlertRepo:
    def __init__(self, alerts, db) -> None:
        self.alerts = alerts
        self.db = db

    async def list(self, tenant_id, *, incident_id=None, status=None, limit=None):
        return list(self.alerts)


def alert(category, status, *, severity="info", title="sensor"):
    import uuid
    from datetime import datetime, timezone

    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        category=category,
        status=status,
        severity=severity,
        title=title,
        description=f"{title} ({category})",
        device_id=DEVICE_ID,
        site_id=None,
        is_root_cause_candidate=False,
        vendor=None,
        region=None,
        monitoring_source_id=None,
        metric_type=None,
        triggered_at=ts,
        last_occurred_at=ts,
        occurrence_count=1,
    )


def build_stack(alerts, *, status=RCA_ELIGIBLE_STATUS):
    """incident repo + real RCAService + real RCALifecycle + trigger + bridge."""
    from app.services.rca_lifecycle import RCALifecycle
    from app.services.rca_service import RCAService

    db = FakeDB()
    row = incident(status=status)
    bus = RecordingBus()
    service = RCAService(
        event_bus=bus,
        alert_repo=FakeAlertRepo(alerts, db),
        incident_repo=FakeIncidentRepo([row], db),
    )
    lifecycle = RCALifecycle(service, bus)
    lifecycle.register()  # the existing RCALifecycle subscriber, unchanged
    trigger = RCATrigger(FakeIncidentRepo([row], db), bus)
    bridge = RCAIncidentCreatedBridge(trigger=trigger, event_bus=bus)
    bridge.register()
    return bus, row, service, trigger, bridge


def test_incident_created_bridge_emits_ai_rca_started():
    bus, _row, _s, _t, bridge = build_stack([alert("snmp", "open")])

    asyncio.run(
        bridge.handle_event(
            DomainEvent(
                event_type=EventType.INCIDENT_CREATED,
                tenant_id=TENANT,
                correlation_id="inc-created-1",
                payload={"incident_id": INCIDENT_ID, "ticket_id": TICKET_ID},
            )
        )
    )
    assert len(bus.published) == 1
    event = bus.published[0]
    assert event.event_type is EventType.AI_RCA_STARTED
    assert event.payload["incident_id"] == INCIDENT_ID
    assert event.payload["ticket_id"] == TICKET_ID
    assert event.correlation_id == "inc-created-1"  # lifecycle correlation kept


def test_bridge_ignores_ineligible_incident():
    bus, row, _s, _t, bridge = build_stack([alert("snmp", "open")], status="resolved")
    asyncio.run(
        bridge.handle_event(
            DomainEvent(
                event_type=EventType.INCIDENT_CREATED,
                tenant_id=TENANT,
                correlation_id="inc-created-2",
                payload={"incident_id": INCIDENT_ID},
            )
        )
    )
    assert bus.published == []
    assert row.root_cause_summary is None


def test_bridge_ignores_event_without_incident_id():
    bus, _row, _s, _t, bridge = build_stack([alert("snmp", "open")])
    asyncio.run(
        bridge.handle_event(
            DomainEvent(
                event_type=EventType.INCIDENT_CREATED, tenant_id=TENANT, payload={}
            )
        )
    )
    assert bus.published == []


def test_repeated_trigger_does_not_rerun_rca():
    """Two-layer idempotency: deterministic correlation id + RCALifecycle guard."""
    source = [alert("icmp", "resolved"), alert("snmp", "open")]
    bus, row, _s, trigger, _b = build_stack(source)

    async def run():
        # Both attempts use the deterministic per-incident correlation id.
        await trigger.trigger_for_incident(INCIDENT_ID, TENANT)
        await trigger.trigger_for_incident(INCIDENT_ID, TENANT)
        for event in list(bus.published):
            await bus._process_event(event)  # noqa: SLF001 - deterministic delivery

    asyncio.run(run())
    ai_rca_events = [
        e for e in bus.published if e.event_type is EventType.AI_RCA_STARTED
    ]
    assert len(ai_rca_events) == 2  # two trigger attempts were emitted...
    assert row.root_cause_summary is not None
    # ...but RCA work happened ONCE: exactly one dependency-FINDINGS entry.
    # supporting_evidence may be persisted as JSON text, so match the exact
    # quoted source value: the findings entry serialises as
    # "source": "dependency_intelligence" and the SOP-context entry as
    # "source": "dependency_intelligence_sop_context", so an exact quoted match
    # cannot conflate them.
    import json

    raw = row.supporting_evidence
    text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
    assert text.count('"source": "dependency_intelligence"') == 1


# ------------------------------------------------------ end-to-end (17, 9-12)


def test_end_to_end_eligible_incident_to_persistence():
    """eligible -> trigger -> AI_RCA_STARTED -> RCALifecycle -> RCAService ->
    enriched analyser -> Dependency Intelligence -> RootCauseResult -> persistence."""
    source = [
        alert("icmp", "resolved", title="PRTG ping"),
        alert("snmp", "open", severity="critical", title="PRTG SNMP CPU Usage"),
    ]
    bus, row, service, trigger, _bridge = build_stack(source)
    assert is_rca_eligible(row) is True  # incident is in the eligible state

    async def run():
        await trigger.trigger_for_incident(INCIDENT_ID, TENANT)
        event = bus.published[-1]
        assert event.event_type is EventType.AI_RCA_STARTED
        await bus._process_event(event)  # delivered to RCALifecycle.handle_event

    asyncio.run(run())

    # Persistence through the existing RCAService path
    assert row.root_cause_summary is not None
    assert "Dependency intelligence:" in row.root_cause_summary
    assert "udp_161" in row.root_cause_summary
    assert row.supporting_evidence is not None

    # Dependency Intelligence reasoning
    analysis = service.analyser.analyze_alerts(row, source)
    finding = analysis.findings[0]
    assert finding.capability == "snmp"
    assert "ipv4" in finding.healthy_dependencies  # ICMP PASS -> IP healthy
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("udp/161", "snmp configuration", "credentials", "acl"):
        assert expect in causes, expect
    assert "device is down" not in causes
    assert "Device is down" in finding.excluded_conclusions
    assert "device is down" not in row.root_cause_summary.lower()
    caps = [e.capability for e in analysis.evidence]
    assert "icmp" in caps and "snmp" in caps


def test_existing_incident_behaviour_remains_intact():
    """Ineligible incidents: no event, no RCA, incident untouched."""
    bus, row, _s, trigger, _b = build_stack([alert("snmp", "open")], status="resolved")
    ok = asyncio.run(trigger.trigger_for_incident(INCIDENT_ID, TENANT))
    assert ok is False
    assert bus.published == []
    assert row.root_cause_summary is None
    assert row.supporting_evidence is None
