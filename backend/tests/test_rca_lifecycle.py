"""
Production RCA Lifecycle V1 tests.

Real path under test:
    AI_RCA_STARTED -> RCALifecycle -> RCAService -> DependencyEnrichedRootCauseAnalyser
    -> Dependency Intelligence -> RootCauseResult

Uses the REAL EventBus, RCAService, RootCauseResult and enriched analyser. Only
the repositories are stubs, so no DB is required. Async via asyncio.run.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.dependency_intelligence.integration import (
    DependencyEnrichedRootCauseAnalyser,
)
from app.services.events.event_bus import DomainEvent, EventBus, EventType
from app.services.rca_lifecycle import (
    RCALifecycle,
    create_rca_service,
    start_rca_lifecycle,
)
from app.services.rca_service import RCAService, RootCauseResult, RuleBasedRootCauseAnalyser

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "22222222-2222-2222-2222-222222222222"
INCIDENT_ID = "33333333-3333-3333-3333-333333333333"
DEVICE_ID = "44444444-4444-4444-4444-444444444444"


class FakeDB:
    def __init__(self) -> None:
        self.commits = 0

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    def add(self, _obj) -> None:
        return None


class FakeIncidentRepo:
    """Tenant-scoped: only returns the incident for its owning tenant."""

    def __init__(self, incident, *, tenant=TENANT, db=None) -> None:
        self.incident = incident
        self.tenant = tenant
        self.db = db or FakeDB()

    async def get(self, incident_id, tenant_id):
        if str(tenant_id) != self.tenant:
            return None
        return self.incident if str(incident_id) == str(self.incident.id) else None


class FakeAlertRepo:
    def __init__(self, alerts, *, db=None) -> None:
        self.alerts = alerts
        self.db = db or FakeDB()
        self.calls: list[tuple] = []

    async def list(self, tenant_id, *, incident_id=None, status=None, limit=None):
        self.calls.append((str(tenant_id), str(incident_id), status, limit))
        return list(self.alerts)


def build_incident(**overrides):
    base = dict(
        id=INCIDENT_ID,
        tenant_id=TENANT,
        device_id=DEVICE_ID,
        site_id=None,
        priority="P2",
        root_cause_summary=None,
        supporting_evidence=None,
        recommended_sop=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def alert(category, status, *, severity="info", title="sensor"):
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


def rca_event(*, tenant=TENANT, incident_id=INCIDENT_ID, correlation=None):
    return DomainEvent(
        event_type=EventType.AI_RCA_STARTED,
        tenant_id=tenant,
        correlation_id=correlation or uuid.uuid4(),
        payload={"incident_id": incident_id},
    )


def build_lifecycle(alerts, *, incident=None):
    incident = incident or build_incident()
    db = FakeDB()
    service = RCAService(
        event_bus=EventBus(),
        alert_repo=FakeAlertRepo(alerts, db=db),
        incident_repo=FakeIncidentRepo(incident, db=db),
    )
    return RCALifecycle(service, service.event_bus), incident, service


# ------------------------------------- production construction (15, 20)


def test_create_rca_service_uses_dependency_enriched_analyser():
    """Inspect the OBJECT GRAPH, not source text."""
    service = create_rca_service()
    assert isinstance(service, RCAService)
    assert isinstance(service.analyser, DependencyEnrichedRootCauseAnalyser)
    assert isinstance(service.analyser.inner, RuleBasedRootCauseAnalyser)


def test_start_rca_lifecycle_builds_a_wired_lifecycle():
    lifecycle = start_rca_lifecycle(event_bus=EventBus())
    assert isinstance(lifecycle, RCALifecycle)
    assert isinstance(lifecycle.service, RCAService)
    assert isinstance(lifecycle.service.analyser, DependencyEnrichedRootCauseAnalyser)


def test_injected_analyser_still_wins():
    sentinel = object()
    assert create_rca_service(analyser=sentinel).analyser is sentinel
    service = RCAService(
        event_bus=EventBus(),
        alert_repo=object(),
        incident_repo=object(),
        analyser=sentinel,
    )
    assert service.analyser is sentinel


# ---------------------------------------------- event path (16, 11, 12)


def test_ai_rca_started_reaches_rootcause_result():
    """Full production event path: real bus + real service + real analyser."""
    lifecycle, incident, _service = build_lifecycle(
        [alert("icmp", "resolved"), alert("snmp", "open", severity="critical")]
    )

    async def run():
        await lifecycle.start()
        try:
            await lifecycle.event_bus.publish_and_wait(rca_event())
        finally:
            await lifecycle.event_bus.stop()

    asyncio.run(run())
    # RCA ran and persisted onto the incident via existing persistence
    assert incident.root_cause_summary is not None
    assert "Dependency intelligence:" in incident.root_cause_summary
    assert "udp_161" in incident.root_cause_summary


def test_incident_from_another_tenant_is_rejected_safely():
    lifecycle, incident, _service = build_lifecycle([alert("snmp", "open")])
    asyncio.run(lifecycle.handle_event(rca_event(tenant=OTHER_TENANT)))
    assert incident.root_cause_summary is None  # untouched, no cross-tenant RCA


def test_missing_incident_does_not_crash():
    incident = build_incident(id=str(uuid.uuid4()))
    lifecycle, _incident, _service = build_lifecycle(
        [alert("snmp", "open")], incident=incident
    )
    asyncio.run(
        lifecycle.handle_event(
            rca_event(incident_id="99999999-9999-9999-9999-999999999999")
        )
    )
    assert incident.root_cause_summary is None


def test_duplicate_event_is_ignored_but_new_trigger_runs():
    lifecycle, incident, _service = build_lifecycle(
        [alert("icmp", "resolved"), alert("snmp", "open")]
    )
    event = rca_event(correlation="fixed-correlation")
    asyncio.run(lifecycle.handle_event(event))
    assert incident.root_cause_summary is not None
    assert lifecycle.is_duplicate(event) is True  # replay is detected
    before = incident.root_cause_summary
    asyncio.run(lifecycle.handle_event(event))
    assert incident.root_cause_summary == before  # no repeated work
    assert lifecycle.is_duplicate(rca_event()) is False  # new trigger allowed


def test_rca_failure_does_not_break_the_event_layer():
    class ExplodingService:
        async def _on_ai_rca_started(self, _event):
            raise RuntimeError("rca exploded")

    lifecycle = RCALifecycle(ExplodingService(), EventBus())
    asyncio.run(lifecycle.handle_event(rca_event()))  # must not raise


# ------------------------------------------------- end to end (17, 18, 19)


def test_snmp_end_to_end_through_production_event_path():
    """PRTG SNMP CPU Usage: ICMP resolved (PASS), SNMP open (FAIL)."""
    source_alerts = [
        alert("icmp", "resolved", title="PRTG ping"),
        alert("snmp", "open", severity="critical", title="PRTG SNMP CPU Usage"),
    ]
    lifecycle, incident, service = build_lifecycle(source_alerts)
    asyncio.run(lifecycle.handle_event(rca_event()))

    analysis = service.analyser.analyze_alerts(incident, source_alerts)
    finding = analysis.findings[0]
    assert finding.capability == "snmp"
    assert "ipv4" in finding.healthy_dependencies  # IP reachability healthy
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("udp/161", "snmp configuration", "credentials", "acl"):
        assert expect in causes, expect
    assert "device is down" not in causes
    # may remain only as an explicit non-conclusion
    assert "Device is down" in finding.excluded_conclusions
    assert "device is down" not in (incident.root_cause_summary or "").lower()


def test_https_end_to_end_through_production_event_path():
    source_alerts = [
        alert("icmp", "resolved"),
        alert("dns", "resolved"),
        alert("https", "open"),
    ]
    lifecycle, incident, service = build_lifecycle(source_alerts)
    asyncio.run(lifecycle.handle_event(rca_event()))

    analysis = service.analyser.analyze_alerts(incident, source_alerts)
    finding = analysis.findings[0]
    assert finding.capability == "https"
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("tcp/443", "tls", "firewall", "listener", "application"):
        assert expect in causes, expect
    assert "dns" in finding.healthy_dependencies
    assert "ipv4" in finding.healthy_dependencies
    assert "application is down" not in (incident.root_cause_summary or "").lower()


def test_bgp_regression_uses_explicit_interface_evidence():
    """No real interface-state source exists; BGP stays explicit-evidence driven."""
    from app.services.dependency_intelligence.analysis import (
        DependencyIntelligenceAnalyzer,
    )
    from app.services.dependency_intelligence.models import Evidence

    analysis = DependencyIntelligenceAnalyzer().analyze(
        DEVICE_ID,
        [Evidence.of("interface_state", "UP"), Evidence.of("bgp", "DOWN")],
    )
    finding = analysis.findings[0]
    assert finding.capability == "bgp"
    assert "interface_state" in finding.healthy_dependencies
    causes = " | ".join(finding.candidate_causes).lower()
    for expect in ("tcp/179", "acl", "bgp configuration", "authentication", "peer"):
        assert expect in causes, expect


def test_result_handling_uses_existing_persistence():
    """RootCauseResult is persisted on the Incident row - no new table."""
    lifecycle, incident, _service = build_lifecycle(
        [alert("icmp", "resolved"), alert("snmp", "open")]
    )
    asyncio.run(lifecycle.handle_event(rca_event()))
    assert isinstance(incident.root_cause_summary, str) and incident.root_cause_summary
    assert incident.supporting_evidence is not None
    # RootCauseResult schema unchanged
    assert RootCauseResult("x", []).root_cause_summary == "x"
